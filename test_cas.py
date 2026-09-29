"""Tests for cas.ContentStore: dedup, refcount GC, concurrency, mark-sweep."""

import os
import random
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cas import ContentStore, ObjectMissing


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = ContentStore(self._tmp.name)
        self.addCleanup(self.store.close)

    def _disk_blob_files(self):
        n = 0
        for _dirpath, _dirs, files in os.walk(self.store.objects_dir):
            n += len(files)
        return n


class TestBasics(StoreTestCase):
    def test_empty_content(self):
        h = self.store.put_root("empty", b"")
        self.assertEqual(self.store.get(h), b"")
        self.assertTrue(self.store.exists(h))
        # dedup: putting empty again must not create a second object
        h2 = self.store.put(b"")
        self.assertEqual(h, h2)
        self.assertEqual(self.store.stats()["objects"], 1)
        self.assertTrue(self.store.verify()["ok"])

    def test_same_content_multiple_refs(self):
        data = b"hello dedup"
        h1 = self.store.put_root("a", data)
        h2 = self.store.put_root("b", data)
        self.assertEqual(h1, h2)
        self.assertEqual(self._disk_blob_files(), 1, "content stored once")
        self.assertEqual(self.store.refcount(h1), 2)
        # removing one root must NOT reclaim: still referenced
        self.store.remove_root("a")
        self.assertTrue(self.store.exists(h1))
        self.assertEqual(self.store.get(h1), data)
        self.assertEqual(self._disk_blob_files(), 1)
        # removing the last reference reclaims
        self.store.remove_root("b")
        self.assertFalse(self.store.exists(h1))
        self.assertEqual(self._disk_blob_files(), 0)

    def test_refcounted_delete_and_cascade(self):
        chunk = self.store.put_root("chunk", b"chunk-data")
        manifest = self.store.put_root("manifest", b"manifest", refs=[chunk])
        self.assertEqual(self.store.refcount(chunk), 2)  # root + manifest edge
        # delete the direct root: chunk survives via manifest
        self.store.remove_root("chunk")
        self.assertTrue(self.store.exists(chunk))
        self.assertEqual(self.store.refcount(chunk), 1)
        # delete manifest: cascades and reclaims both
        self.store.remove_root("manifest")
        self.assertFalse(self.store.exists(manifest))
        self.assertFalse(self.store.exists(chunk))
        self.assertEqual(self._disk_blob_files(), 0)

    def test_release_only_reclaims_at_zero(self):
        h = self.store.put(b"x" * 32)
        handles = [self.store.ref(h) for _ in range(3)]
        self.assertEqual(self.store.refcount(h), 3)
        self.store.release(handles[0])
        self.store.release(handles[1])
        self.assertTrue(self.store.exists(h))
        self.store.release(handles[2])
        self.assertFalse(self.store.exists(h))
        with self.assertRaises(ObjectMissing):
            self.store.get(h)
        with self.assertRaises(ObjectMissing):
            self.store.release(handles[2])  # double release

    def test_missing_object_raises(self):
        with self.assertRaises(ObjectMissing):
            self.store.get("0" * 64)
        with self.assertRaises(ObjectMissing):
            self.store.ref("0" * 64)
        with self.assertRaises(ObjectMissing):
            self.store.put(b"childless-parent", refs=["0" * 64])


class TestMarkSweep(StoreTestCase):
    def test_sweep_collects_unreachable_and_repairs_drift(self):
        keep = self.store.put_root("keep", b"keep-me")
        child = self.store.put(b"child-of-keep")
        self.store.put_root("parent", b"parent", refs=[child])
        orphan = self.store.put(b"orphan-garbage")  # refcount 0, unreachable

        # deliberately corrupt refcounts (simulating drift/bug)
        self.store._db.execute(
            "UPDATE objects SET refcount=refcount+5 WHERE hash=?", (keep,)
        )
        self.store._db.execute(
            "UPDATE objects SET refcount=0 WHERE hash=?", (child,)
        )
        self.store._db.commit()
        before = self.store.verify()
        self.assertFalse(before["ok"], "drift must be detected by verify()")

        report = self.store.sweep()

        # orphan garbage reclaimed, reachable objects kept
        self.assertFalse(self.store.exists(orphan))
        self.assertTrue(self.store.exists(keep))
        self.assertTrue(self.store.exists(child))
        self.assertEqual(report["swept"], 1)
        self.assertGreaterEqual(report["bytes_reclaimed"], len(b"orphan-garbage"))
        # drift repaired and reported
        fixed = {c["hash"] for c in report["refcount_corrections"]}
        self.assertIn(keep, fixed)
        self.assertIn(child, fixed)
        self.assertEqual(self.store.refcount(keep), 1)
        self.assertEqual(self.store.refcount(child), 1)
        # validation after repair is clean
        after = self.store.verify()
        self.assertTrue(after["ok"], after["problems"])

    def test_sweep_recovers_from_zero_refcount_on_live_object(self):
        # worst-case drift: a still-referenced object has refcount 0,
        # which would let refcount-GC wrongly reclaim it
        h = self.store.put_root("live", b"important")
        self.store._db.execute(
            "UPDATE objects SET refcount=0 WHERE hash=?", (h,)
        )
        self.store._db.commit()
        report = self.store.sweep()
        self.assertTrue(self.store.exists(h), "sweep must keep rooted object")
        self.assertEqual(self.store.refcount(h), 1)
        self.assertEqual(len(report["refcount_corrections"]), 1)
        self.assertTrue(self.store.verify()["ok"])


class TestConcurrency(StoreTestCase):
    """Writers and the collector run at full speed concurrently.

    Safety invariant under test: a blob that was just referenced
    (rooted) is never deleted by concurrent reclamation.
    """

    def test_concurrent_writes_and_gc(self):
        pool = [bytes([i]) * 64 for i in range(64)]  # shared contents
        errors = []
        stop = threading.Event()

        def writer(tid):
            rng = random.Random(tid)
            name = f"root-{tid}"
            try:
                while not stop.is_set():
                    data = rng.choice(pool)
                    h = self.store.put_root(name, data)
                    # the just-referenced blob MUST be readable
                    got = self.store.get(h)
                    if got != data:
                        errors.append(f"tid={tid} content mismatch for {h}")
                        return
                    if rng.random() < 0.3:
                        # anchor a second root, then drop both
                        h2 = self.store.put_root(f"{name}-tmp", data)
                        self.store.remove_root(f"{name}-tmp")
                        if not self.store.exists(h2):
                            errors.append(f"tid={tid} {h2} vanished while rooted")
                            return
            except Exception as exc:  # noqa: BLE001
                errors.append(f"tid={tid} {type(exc).__name__}: {exc}")

        def sweeper():
            try:
                while not stop.is_set():
                    self.store.sweep()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"sweeper {type(exc).__name__}: {exc}")

        def reader():
            try:
                while not stop.is_set():
                    for h in list(self.store.roots().values()):
                        try:
                            handle = self.store.ref(h)
                        except ObjectMissing:
                            continue  # legitimately unrooted meanwhile
                        try:
                            self.store.get(h)  # pinned: must never fail
                        finally:
                            self.store.release(handle)
            except ObjectMissing as exc:
                errors.append(f"reader: pinned object missing: {exc}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"reader {type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(8)]
        threads.append(threading.Thread(target=sweeper))
        threads.append(threading.Thread(target=reader))
        for t in threads:
            t.start()
        import time

        time.sleep(3.0)
        stop.set()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        # final state must be fully consistent
        report = self.store.sweep()
        self.assertEqual(report["refcount_corrections"], [])
        v = self.store.verify()
        self.assertTrue(v["ok"], v["problems"])

    def test_ref_release_race_on_same_object(self):
        # Hammer ref/release on one shared hash while a sweeper runs;
        # a permanent root keeps the object alive the whole time.
        data = b"hot-object"
        h = self.store.put_root("anchor", data)
        errors = []
        stop = threading.Event()

        def churn():
            try:
                while not stop.is_set():
                    handle = self.store.ref(h)
                    self.store.release(handle)
            except ObjectMissing as exc:
                errors.append(f"churn: {exc}")

        def sweeper():
            while not stop.is_set():
                self.store.sweep()

        threads = [threading.Thread(target=churn) for _ in range(8)]
        threads.append(threading.Thread(target=sweeper))
        for t in threads:
            t.start()
        import time

        time.sleep(2.0)
        stop.set()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(self.store.get(h), data)
        self.assertEqual(self.store.refcount(h), 1)
        self.assertTrue(self.store.verify()["ok"])


class TestManySmallObjects(StoreTestCase):
    def test_many_small_objects(self):
        n = 20_000
        hashes = []
        for i in range(n):
            # 10% unique contents -> heavy dedup expected
            hashes.append(self.store.put(f"payload-{i % 2000}".encode()))
        unique = len(set(hashes))
        self.assertEqual(unique, 2000)
        self.assertEqual(self.store.stats()["objects"], unique)
        for i, h in enumerate(hashes):
            self.assertEqual(self.store.get(h), f"payload-{i % 2000}".encode())
        self.assertTrue(self.store.verify()["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
