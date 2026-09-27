#!/usr/bin/env python3
"""Regression tests for the retention cleanup fix.

Run: python3 -m unittest test_cleanup -v
"""

import errno
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from storage_cleanup import (
    DataStore,
    StorageFullError,
    dry_run,
    evaluate_all,
    execute,
)

NOW = 1_700_000_000.0
MIN_AGE = 3600.0
OLD = NOW - 10 * 86400


class CleanupTestBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="retention-test-"))
        self.store = DataStore(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def decisions_by_id(self, decisions):
        return {d["id"]: d for d in decisions}

    def run_full_cycle(self, now=NOW):
        plan, decisions = dry_run(self.store, now=now, min_age=MIN_AGE)
        audit_path, audit = execute(self.store, plan, now=now)
        return plan, decisions, audit_path, audit


class TestPolicy(CleanupTestBase):
    def test_empty_directory(self):
        plan, decisions, audit_path, audit = self.run_full_cycle()
        self.assertEqual(decisions, [])
        self.assertEqual(audit, [])
        self.assertTrue(plan.exists())       # plan/audit still produced
        self.assertTrue(audit_path.exists())

    def test_all_deletable(self):
        self.store.write("a", b"aaa", now=OLD)
        self.store.write("b", b"bbb", now=OLD)
        self.store._save_refs({})            # manifest present, nothing referenced
        _, decisions, _, audit = self.run_full_cycle()
        self.assertEqual({d["id"] for d in decisions}, {"a", "b"})
        self.assertTrue(all(d["action"] == "delete" for d in decisions))
        self.assertEqual(sorted(a["id"] for a in audit if a["outcome"] == "deleted"),
                         ["a", "b"])
        self.assertFalse(self.store.blob_path("a").exists())
        self.assertFalse(self.store.blob_path("b").exists())

    def test_referenced_blob_kept(self):
        self.store.write("keep-me", b"in use", now=OLD)
        self.store.add_ref("keep-me", "service-x")
        _, decisions, _, _ = self.run_full_cycle()
        d = self.decisions_by_id(decisions)["keep-me"]
        self.assertEqual(d["action"], "keep")
        self.assertEqual(d["reason"], "KEEP_REFERENCED")
        self.assertTrue(self.store.blob_path("keep-me").exists())

    def test_archive_in_progress_kept(self):
        self.store.write("archiving", b"data", now=OLD)
        self.store._save_refs({})
        self.store.start_archive("archiving", task="nightly", now=NOW - 60)
        _, decisions, _, _ = self.run_full_cycle()
        d = self.decisions_by_id(decisions)["archiving"]
        self.assertEqual(d["action"], "keep")
        self.assertEqual(d["reason"], "KEEP_ARCHIVE_ACTIVE")
        self.assertTrue(self.store.blob_path("archiving").exists())

    def test_recently_written_kept(self):
        self.store.write("fresh", b"new", now=NOW - 10)  # within grace period
        self.store._save_refs({})
        _, decisions, _, _ = self.run_full_cycle()
        d = self.decisions_by_id(decisions)["fresh"]
        self.assertEqual(d["action"], "keep")
        self.assertEqual(d["reason"], "KEEP_WITHIN_GRACE")
        self.assertTrue(self.store.blob_path("fresh").exists())

    def test_missing_refs_manifest_failsafe(self):
        self.store.write("old", b"data", now=OLD)
        # no refs.json at all -> cleaner must refuse to delete
        _, decisions, _, audit = self.run_full_cycle()
        d = self.decisions_by_id(decisions)["old"]
        self.assertEqual(d["action"], "keep")
        self.assertEqual(d["reason"], "KEEP_REFS_MISSING")
        self.assertEqual(audit, [])
        self.assertTrue(self.store.blob_path("old").exists())

    def test_missing_meta_failsafe(self):
        self.store.write("no-meta", b"data", now=OLD)
        self.store._save_refs({})
        self.store.meta_path("no-meta").unlink()
        _, decisions, _, _ = self.run_full_cycle()
        d = self.decisions_by_id(decisions)["no-meta"]
        self.assertEqual(d["action"], "keep")
        self.assertEqual(d["reason"], "KEEP_META_MISSING")

    def test_mtime_and_atime_not_used_for_decision(self):
        # Old mtime but referenced -> keep (mtime would have said delete).
        self.store.write("referenced", b"x", now=OLD)
        self.store.add_ref("referenced", "svc")
        os.utime(self.store.blob_path("referenced"), (OLD, OLD))
        # Fresh mtime but unreferenced and old per store meta -> delete
        # (mtime would have said keep).
        self.store.write("touched", b"y", now=OLD)
        self.store._save_refs({"referenced": ["svc"]})
        os.utime(self.store.blob_path("touched"), (NOW, NOW))
        decisions = evaluate_all(self.store, NOW, MIN_AGE)
        by_id = self.decisions_by_id(decisions)
        self.assertEqual(by_id["referenced"]["action"], "keep")
        self.assertEqual(by_id["touched"]["action"], "delete")


class TestDryRunAndAudit(CleanupTestBase):
    def test_dry_run_deletes_nothing_and_records_rationale(self):
        self.store.write("victim", b"old unused", now=OLD)
        self.store.write("used", b"still needed", now=OLD)
        self.store.add_ref("used", "svc")
        plan, decisions = dry_run(self.store, now=NOW, min_age=MIN_AGE)
        # dry-run changed nothing
        self.assertTrue(self.store.blob_path("victim").exists())
        self.assertTrue(self.store.blob_path("used").exists())
        # plan file contains candidate list with per-item rationale
        lines = [json.loads(l) for l in plan.read_text().splitlines()]
        entries = {l["id"]: l for l in lines if l["type"] == "decision"}
        self.assertEqual(entries["victim"]["action"], "delete")
        self.assertEqual(entries["victim"]["reason"], "DELETE_ELIGIBLE")
        self.assertIn("unreferenced", entries["victim"]["detail"])
        self.assertEqual(entries["used"]["action"], "keep")
        self.assertEqual(entries["used"]["reason"], "KEEP_REFERENCED")

    def test_execute_audit_traces_every_deletion(self):
        self.store.write("gone", b"old", now=OLD)
        self.store._save_refs({})
        plan, _, audit_path, audit = self.run_full_cycle()
        self.assertEqual(len(audit), 1)
        record = audit[0]
        self.assertEqual(record["outcome"], "deleted")
        self.assertEqual(record["plan_reason"], "DELETE_ELIGIBLE")
        self.assertIn("unreferenced", record["plan_detail"])
        self.assertEqual(record["plan"], plan.name)
        # audit log on disk carries the same trace
        lines = [json.loads(l) for l in audit_path.read_text().splitlines()]
        disk_record = next(l for l in lines if l.get("type") == "audit")
        self.assertEqual(disk_record["id"], "gone")
        self.assertEqual(disk_record["plan_reason"], "DELETE_ELIGIBLE")


class TestConcurrency(CleanupTestBase):
    def test_file_replaced_between_plan_and_execute_is_kept(self):
        self.store.write("hot", b"stale", now=OLD)
        self.store._save_refs({})
        plan, decisions = dry_run(self.store, now=NOW, min_age=MIN_AGE)
        self.assertEqual(decisions[0]["action"], "delete")
        # concurrent writer atomically replaces the blob after the plan
        self.store.write("hot", b"fresh, in use", now=NOW)
        _, audit = execute(self.store, plan, now=NOW)
        self.assertEqual(audit[0]["outcome"], "skipped")
        self.assertEqual(audit[0]["recheck"], "changed")
        self.assertEqual(self.store.read("hot"), b"fresh, in use")

    def test_ref_added_between_plan_and_execute_is_kept(self):
        self.store.write("late-ref", b"data", now=OLD)
        self.store._save_refs({})
        plan, _ = dry_run(self.store, now=NOW, min_age=MIN_AGE)
        self.store.add_ref("late-ref", "new-consumer")  # race: new reference
        _, audit = execute(self.store, plan, now=NOW)
        self.assertEqual(audit[0]["outcome"], "skipped")
        self.assertEqual(audit[0]["recheck"], "policy")
        self.assertEqual(audit[0]["recheck_reason"], "KEEP_REFERENCED")
        self.assertTrue(self.store.blob_path("late-ref").exists())

    def test_archive_started_between_plan_and_execute_is_kept(self):
        self.store.write("late-lock", b"data", now=OLD)
        self.store._save_refs({})
        plan, _ = dry_run(self.store, now=NOW, min_age=MIN_AGE)
        self.store.start_archive("late-lock", task="ad-hoc", now=NOW)
        _, audit = execute(self.store, plan, now=NOW)
        self.assertEqual(audit[0]["outcome"], "skipped")
        self.assertEqual(audit[0]["recheck_reason"], "KEEP_ARCHIVE_ACTIVE")
        self.assertTrue(self.store.blob_path("late-lock").exists())


class TestDiskFull(CleanupTestBase):
    def test_write_on_full_disk_raises_and_leaves_no_partial_state(self):
        with mock.patch("os.replace",
                        side_effect=OSError(errno.ENOSPC, "No space left on device")):
            with self.assertRaises(StorageFullError):
                self.store.write("doomed", b"payload", now=NOW)
        self.assertFalse(self.store.blob_path("doomed").exists())
        self.assertFalse(self.store.meta_path("doomed").exists())
        leftovers = list((self.root / "data").iterdir())
        self.assertEqual(leftovers, [])

    def test_delete_failure_is_recorded_and_other_deletions_continue(self):
        self.store.write("ok", b"1", now=OLD)
        self.store.write("stuck", b"2", now=OLD)
        self.store._save_refs({})
        plan, _ = dry_run(self.store, now=NOW, min_age=MIN_AGE)

        real_unlink = Path.unlink

        def flaky_unlink(self_path, *args, **kwargs):
            if self_path.name == "stuck.blob":
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_unlink(self_path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", flaky_unlink):
            _, audit = execute(self.store, plan, now=NOW)
        by_id = {a["id"]: a for a in audit}
        self.assertEqual(by_id["ok"]["outcome"], "deleted")
        self.assertEqual(by_id["stuck"]["outcome"], "error")
        self.assertIn("unlink failed", by_id["stuck"]["detail"])
        self.assertFalse(self.store.blob_path("ok").exists())
        self.assertTrue(self.store.blob_path("stuck").exists())


if __name__ == "__main__":
    unittest.main()
