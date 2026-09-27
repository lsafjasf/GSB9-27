#!/usr/bin/env python3
"""Reproduction of the mis-deletion bug + regression tests for the fix."""

import errno
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import cleanup
import legacy_cleanup

NOW = 1_700_000_000.0
RETENTION = 3600.0
OLD_AGE = 10 * RETENTION  # comfortably outside the retention window


class CleanupTestBase(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix="cleanup-test-")
        self.addCleanup(shutil.rmtree, self.data_dir, ignore_errors=True)
        self.referenced = set()
        self.archive_in_progress = set()
        self.archive_completed = set()

    # -- helpers ---------------------------------------------------------
    def path(self, blob_id):
        return os.path.join(self.data_dir, blob_id)

    def make_blob(self, blob_id, age, referenced=False, archive=None,
                  atime=None):
        """Create a blob whose mtime is ``age`` seconds before NOW."""
        path = self.path(blob_id)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"payload of {blob_id}\n")
        mtime = NOW - age
        os.utime(path, (mtime if atime is None else atime, mtime))
        if referenced:
            self.referenced.add(blob_id)
        if archive == "in_progress":
            self.archive_in_progress.add(blob_id)
        elif archive == "completed":
            self.archive_completed.add(blob_id)
        return path

    def write_state(self):
        with open(self.path(cleanup.REFS_FILE), "w", encoding="utf-8") as fh:
            json.dump(sorted(self.referenced), fh)
        with open(self.path(cleanup.ARCHIVE_STATE_FILE), "w",
                  encoding="utf-8") as fh:
            json.dump({
                "in_progress": sorted(self.archive_in_progress),
                "completed": sorted(self.archive_completed),
            }, fh)

    def scan(self):
        return cleanup.scan(self.data_dir, RETENTION, now=NOW)

    def read_audit(self):
        records = []
        with open(self.path(cleanup.AUDIT_FILE), encoding="utf-8") as fh:
            for line in fh:
                records.append(json.loads(line))
        return records


class TestBugReproduction(CleanupTestBase):
    """The legacy implementation deletes in-use data because the archive
    task's reads distort atime. These tests pin the buggy behaviour and
    show the fixed implementation surviving the exact same scenario."""

    def test_legacy_deletes_referenced_blob_after_archive_read(self):
        # Blob is still referenced, but the archive task read it, so
        # atime > mtime and the legacy policy thinks it is archived.
        path = self.make_blob("blob_in_use", OLD_AGE, referenced=True,
                              archive="in_progress", atime=NOW)
        self.write_state()

        deleted = legacy_cleanup.run(self.data_dir, RETENTION, now=NOW)

        # BUG reproduced: in-use data was deleted.
        self.assertIn(path, deleted)
        self.assertFalse(os.path.exists(path))

    def test_fixed_survives_identical_scenario(self):
        path = self.make_blob("blob_in_use", OLD_AGE, referenced=True,
                              archive="in_progress", atime=NOW)
        self.write_state()

        now, decisions = self.scan()
        result = cleanup.execute(self.data_dir, decisions, now, RETENTION)

        self.assertEqual(result["deleted"], [])
        self.assertTrue(os.path.exists(path))


class TestFixedPolicy(CleanupTestBase):
    def test_referenced_blob_is_never_eligible(self):
        self.make_blob("ref", OLD_AGE, referenced=True, archive="completed")
        self.write_state()
        _, decisions = self.scan()
        self.assertFalse(decisions[0].eligible)
        self.assertFalse(decisions[0].criteria["referenced"]["ok"])

    def test_in_progress_archive_is_never_eligible(self):
        self.make_blob("archiving", OLD_AGE, archive="in_progress")
        self.write_state()
        _, decisions = self.scan()
        self.assertFalse(decisions[0].eligible)
        self.assertFalse(decisions[0].criteria["archive_in_progress"]["ok"])

    def test_recently_written_blob_is_never_eligible(self):
        self.make_blob("fresh", age=RETENTION / 2, archive="completed")
        self.write_state()
        _, decisions = self.scan()
        self.assertFalse(decisions[0].eligible)
        self.assertFalse(decisions[0].criteria["age_seconds"]["ok"])

    def test_external_time_change_does_not_fool_policy(self):
        # Someone rewinds mtime far into the past and bumps atime; the blob
        # is still referenced, so it must survive anyway.
        path = self.make_blob("ref", OLD_AGE, referenced=True,
                              archive="completed", atime=NOW)
        os.utime(path, (NOW, NOW - 100 * RETENTION))
        self.write_state()
        now, decisions = self.scan()
        result = cleanup.execute(self.data_dir, decisions, now, RETENTION)
        self.assertEqual(result["deleted"], [])
        self.assertTrue(os.path.exists(path))

    def test_fully_eligible_blob_is_deleted_and_audited(self):
        self.make_blob("dead", OLD_AGE, archive="completed")
        self.write_state()
        now, decisions = self.scan()
        result = cleanup.execute(self.data_dir, decisions, now, RETENTION)

        self.assertEqual(result["deleted"], ["dead"])
        self.assertFalse(os.path.exists(self.path("dead")))

        deleted = [r for r in self.read_audit() if r["event"] == "deleted"]
        self.assertEqual(len(deleted), 1)
        criteria = deleted[0]["criteria"]
        # Every criterion is traceable and passed.
        self.assertEqual(set(criteria), {
            "referenced", "archive_in_progress",
            "archive_completed", "age_seconds",
        })
        self.assertTrue(all(c["ok"] for c in criteria.values()))

    def test_dry_run_lists_candidates_with_reasons_and_deletes_nothing(self):
        self.make_blob("dead", OLD_AGE, archive="completed")
        self.make_blob("ref", OLD_AGE, referenced=True, archive="completed")
        self.write_state()
        now, decisions = self.scan()

        report = cleanup.dry_run_report(self.data_dir, decisions, now,
                                        RETENTION)

        self.assertEqual(report["summary"],
                         {"scanned": 2, "candidates": 1, "excluded": 1})
        self.assertEqual(report["candidates"][0]["blob_id"], "dead")
        self.assertEqual(report["excluded"][0]["blob_id"], "ref")
        self.assertIn("criteria", report["candidates"][0])
        self.assertIn("criteria", report["excluded"][0])
        # Dry-run is side-effect free.
        self.assertTrue(os.path.exists(self.path("dead")))
        self.assertTrue(os.path.exists(self.path("ref")))


class TestEdgeCases(CleanupTestBase):
    def test_empty_directory(self):
        self.write_state()
        now, decisions = self.scan()
        report = cleanup.dry_run_report(self.data_dir, decisions, now,
                                        RETENTION)
        self.assertEqual(report["summary"]["scanned"], 0)
        result = cleanup.execute(self.data_dir, decisions, now, RETENTION)
        self.assertEqual(result["deleted"], [])

    def test_all_deletable(self):
        for i in range(5):
            self.make_blob(f"dead{i}", OLD_AGE, archive="completed")
        self.write_state()
        now, decisions = self.scan()
        result = cleanup.execute(self.data_dir, decisions, now, RETENTION)
        self.assertEqual(sorted(result["deleted"]),
                         [f"dead{i}" for i in range(5)])
        remaining = [n for n in os.listdir(self.data_dir)
                     if n not in cleanup.META_FILES]
        self.assertEqual(remaining, [])

    def test_missing_reference_info_refuses_to_delete(self):
        self.make_blob("dead", OLD_AGE, archive="completed")
        # No refs.json written at all.
        with self.assertRaises(cleanup.CleanupError):
            self.scan()
        self.assertTrue(os.path.exists(self.path("dead")))
        # CLI surfaces the failure with a non-zero exit code.
        self.assertEqual(cleanup.main([self.data_dir, "--execute",
                                       "--now", str(NOW),
                                       "--retention-seconds", str(RETENTION)]),
                         2)

    def test_disk_full_on_audit_log_aborts_before_any_delete(self):
        self.make_blob("dead", OLD_AGE, archive="completed")
        self.write_state()
        now, decisions = self.scan()
        enospc = OSError(errno.ENOSPC, "No space left on device")
        with mock.patch.object(cleanup, "_open_audit", side_effect=enospc):
            with self.assertRaises(cleanup.CleanupError):
                cleanup.execute(self.data_dir, decisions, now, RETENTION)
        # Fail-safe: without an audit trail nothing may be deleted.
        self.assertTrue(os.path.exists(self.path("dead")))


class TestConcurrency(CleanupTestBase):
    def test_blob_modified_during_cleanup_is_not_deleted(self):
        # Eligible at scan time, but a concurrent writer rewrites it between
        # scan and delete; the pre-delete re-check must skip it.
        path = self.make_blob("racy", OLD_AGE, archive="completed")
        self.write_state()
        now, decisions = self.scan()

        def concurrent_writer(changed_path):
            with open(changed_path, "a", encoding="utf-8") as fh:
                fh.write("concurrent append\n")

        result = cleanup.execute(self.data_dir, decisions, now, RETENTION,
                                 before_delete=concurrent_writer)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(result["skipped"],
                         [{"blob_id": "racy", "reason": "modified_since_scan"}])
        self.assertTrue(os.path.exists(path))

    def test_new_writes_during_cleanup_survive(self):
        for i in range(3):
            self.make_blob(f"dead{i}", OLD_AGE, archive="completed")
        self.write_state()
        now, decisions = self.scan()

        stop = threading.Event()
        created = []

        def writer():
            n = 0
            while not stop.is_set():
                name = f"fresh{n}"
                with open(self.path(name), "w", encoding="utf-8") as fh:
                    fh.write("new data\n")
                created.append(name)
                n += 1

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            result = cleanup.execute(self.data_dir, decisions, now, RETENTION)
        finally:
            stop.set()
            thread.join()

        self.assertEqual(sorted(result["deleted"]),
                         ["dead0", "dead1", "dead2"])
        for name in created:
            self.assertTrue(os.path.exists(self.path(name)), name)


if __name__ == "__main__":
    unittest.main()
