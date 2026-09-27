"""Regression tests for the fixed memory pool.

Covers:
  * coalescing and reuse basics
  * workload 1: extreme mix of tiny and huge allocations
  * workload 2: high-frequency alloc/free churn within one size class
  * workload 3: long-lived large objects surrounded by small-object churn
  * concurrency: parallel alloc/free must never move, release or corrupt
    a live block (canary + structural invariants)

Run: python3 -m unittest test_mempool -v
"""

import random
import threading
import unittest

from mempool import ALIGNMENT, MemoryPool, OutOfMemoryError

CAPACITY = 1 << 18  # 256 KiB


class TestBasicBehaviour(unittest.TestCase):
    def test_full_coalescing(self):
        pool = MemoryPool(CAPACITY)
        offsets = [pool.alloc(1000) for _ in range(8)]
        for off in offsets:
            pool.free(off)
        self.assertEqual(pool.total_free(), CAPACITY)
        self.assertEqual(pool.largest_free_block(), CAPACITY)
        self.assertEqual(pool.fragmentation(), 0.0)
        pool.check_invariants()

    def test_adjacent_merge_with_neighbours(self):
        pool = MemoryPool(CAPACITY)
        a = pool.alloc(256)
        b = pool.alloc(256)
        c = pool.alloc(256)
        pool.free(b)          # hole between a and c
        pool.free(a)          # merges with b (next)
        pool.free(c)          # merges with a+b (previous)
        self.assertEqual(pool.largest_free_block(), CAPACITY)
        pool.check_invariants()

    def test_double_free_rejected(self):
        pool = MemoryPool(CAPACITY)
        off = pool.alloc(64)
        pool.free(off)
        with self.assertRaises(ValueError):
            pool.free(off)

    def test_invalid_arguments(self):
        pool = MemoryPool(CAPACITY)
        with self.assertRaises(ValueError):
            pool.alloc(0)
        with self.assertRaises(ValueError):
            pool.free(12345)

    def test_alignment_rounding(self):
        pool = MemoryPool(CAPACITY)
        off = pool.alloc(1)
        self.assertEqual(off % ALIGNMENT, 0)
        self.assertEqual(pool._allocated[off], ALIGNMENT)
        pool.check_invariants()

    def test_oom_only_when_truly_full(self):
        pool = MemoryPool(1024)
        off = pool.alloc(1024)
        with self.assertRaises(OutOfMemoryError):
            pool.alloc(1)
        pool.free(off)
        pool.alloc(1024)  # whole arena reusable again
        pool.check_invariants()


class TestWorkloads(unittest.TestCase):
    """Each workload asserts the fragmentation stays bounded and that a
    large allocation still succeeds afterwards -- the exact scenario that
    failed on the buggy implementation."""

    def test_extreme_size_mix(self):
        pool = MemoryPool(CAPACITY)
        rng = random.Random(7)
        live = []
        for _ in range(1500):
            size = rng.choice([16, 17, 24, 32 << 10, 48 << 10, 16])
            try:
                live.append(pool.alloc(size))
            except OutOfMemoryError:
                pass
            if live and rng.random() < 0.7:
                pool.free(live.pop(rng.randrange(len(live))))
        for off in live:
            pool.free(off)
        self.assertLess(pool.fragmentation(), 0.05)
        off = pool.alloc(64 << 10)  # 64 KiB still available
        pool.free(off)
        pool.check_invariants()

    def test_same_class_churn(self):
        pool = MemoryPool(CAPACITY)
        keep = []
        for _ in range(20000):
            off = pool.alloc(512)
            if len(keep) < 64:
                keep.append(off)
            else:
                pool.free(off)
        for off in keep:
            pool.free(off)
        # churn within one class must not erode the arena at all
        self.assertEqual(pool.total_free(), CAPACITY)
        self.assertEqual(pool.fragmentation(), 0.0)
        pool.check_invariants()

    def test_long_lived_large_objects(self):
        pool = MemoryPool(CAPACITY)
        rng = random.Random(11)
        veterans = [pool.alloc(32 << 10) for _ in range(3)]  # 3 x 32 KiB
        live = []
        for _ in range(3000):
            if len(live) < 64:  # churn around a steady working set
                try:
                    live.append(pool.alloc(rng.randint(24, 512)))
                except OutOfMemoryError:
                    pass
            else:
                pool.free(live.pop(rng.randrange(len(live))))
        # a fourth long-lived large object must still fit
        veteran4 = pool.alloc(32 << 10)
        self.assertLess(pool.fragmentation(), 0.10)
        for off in live + veterans + [veteran4]:
            pool.free(off)
        self.assertEqual(pool.fragmentation(), 0.0)
        pool.check_invariants()


class TestConcurrency(unittest.TestCase):
    def test_concurrent_alloc_free_invariants(self):
        """8 threads x 4000 ops of random alloc/write/verify/free.

        Invariants asserted:
          C1 a live block is never moved: the offset returned by alloc()
             is the one freed, and its canary bytes stay intact for the
             block's whole lifetime (no compaction may touch it).
          C2 live blocks never overlap: each block keeps a unique canary
             pattern; any overlap would corrupt a peer's canary and be
             caught before free().
          C3 no double free / no leak: after all threads join, every
             still-live block is freed exactly once and the arena is
             fully recovered (fragmentation == 0).
          C4 structural invariants hold at every quiescent point.
        """
        pool = MemoryPool(CAPACITY)
        errors = []
        stop = threading.Event()

        def worker(seed):
            rng = random.Random(seed)
            live = {}  # offset -> (size, canary); only this thread touches it
            try:
                for _ in range(4000):
                    if stop.is_set():
                        return
                    if live and rng.random() < 0.5:
                        off = rng.choice(list(live))
                        size, canary = live.pop(off)
                        block = pool.arena[off:off + size]
                        if block != bytes([canary]) * size:
                            raise AssertionError(
                                "C1/C2 violated: live block at %d was "
                                "moved or corrupted" % off)
                        pool.free(off)
                    else:
                        size = rng.randint(1, 2048)
                        try:
                            off = pool.alloc(size)
                        except OutOfMemoryError:
                            continue
                        canary = rng.randrange(1, 256)
                        pool.arena[off:off + size] = bytes([canary]) * size
                        live[off] = (size, canary)
                # drain: verify and free everything still held by this thread
                for off, (size, canary) in live.items():
                    if pool.arena[off:off + size] != bytes([canary]) * size:
                        raise AssertionError(
                            "C1/C2 violated at drain: block %d corrupt" % off)
                    pool.free(off)
            except Exception as exc:  # noqa: BLE001 - reported to main thread
                errors.append(exc)
                stop.set()

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        # C3: every block freed exactly once -> arena fully recovered
        self.assertEqual(pool.total_free(), CAPACITY)
        self.assertEqual(pool.fragmentation(), 0.0)
        # C4
        pool.check_invariants()

    def test_quiescent_invariants_under_load(self):
        """Main thread snapshots invariants while workers churn."""
        pool = MemoryPool(CAPACITY)
        stop = threading.Event()
        errors = []

        def worker(seed):
            rng = random.Random(seed)
            live = []
            while not stop.is_set():
                try:
                    if live and rng.random() < 0.5:
                        pool.free(live.pop(rng.randrange(len(live))))
                    else:
                        live.append(pool.alloc(rng.randint(16, 1024)))
                except OutOfMemoryError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
                    stop.set()

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(4)]
        for t in threads:
            t.start()
        try:
            for _ in range(200):
                self.assertTrue(pool.check_invariants())
        finally:
            stop.set()
            for t in threads:
                t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
