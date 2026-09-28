"""Self-tests for occ.py: conflict detection, phantoms, fairness, retries."""

import threading
import time
import unittest

from occ import (
    ConflictError,
    OCCStore,
    PhantomConflict,
    RetryExhausted,
)


def increment(tx, key):
    tx.put(key, (tx.get(key, 0) or 0) + 1)


class TestSingleTransaction(unittest.TestCase):
    def test_commit_and_read_back(self):
        store = OCCStore()
        store.run(lambda tx: tx.put("a", 1))
        self.assertEqual(store.get("a"), 1)

    def test_read_your_writes_and_delete(self):
        store = OCCStore()
        store.put("a", 1)

        def body(tx):
            self.assertEqual(tx.get("a"), 1)
            tx.put("a", 2)
            self.assertEqual(tx.get("a"), 2)
            tx.delete("a")
            self.assertIsNone(tx.get("a"))

        store.run(body)
        self.assertIsNone(store.get("a"))

    def test_scan_sees_own_writes(self):
        store = OCCStore()
        for i in range(5):
            store.put(f"k{i}", i)

        def body(tx):
            tx.put("k9", 9)
            tx.delete("k0")
            return [k for k, _ in tx.scan("k", "k;")]

        self.assertEqual(store.run(body), ["k1", "k2", "k3", "k4", "k9"])


class TestConflictDetection(unittest.TestCase):
    def test_read_write_conflict(self):
        store = OCCStore()
        store.put("x", 0)
        tx = __import__("occ").Transaction(store)
        tx.get("x")                      # reads version 1
        store.put("x", 99)               # someone else commits
        with self.assertRaises(ConflictError):
            tx.commit()

    def test_no_conflict_when_untouched(self):
        store = OCCStore()
        store.put("x", 0)
        store.put("y", 0)
        tx = __import__("occ").Transaction(store)
        tx.get("x")
        store.put("y", 1)                # key outside the read set
        tx.commit()                      # must succeed

    def test_blind_writes_do_not_conflict(self):
        store = OCCStore()
        results = []
        barrier = threading.Barrier(2)

        def writer(value):
            def body(tx):
                barrier.wait(timeout=5)
                tx.put("w", value)
            store.run(body)
            results.append(value)

        threads = [threading.Thread(target=writer, args=(v,)) for v in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertIn(store.get("w"), (1, 2))  # last committer wins


class TestPhantom(unittest.TestCase):
    def setUp(self):
        self.store = OCCStore()
        for i in range(5):
            self.store.put(f"user:{i}", i)

    def test_insert_into_scanned_range_aborts(self):
        tx = __import__("occ").Transaction(self.store)
        rows = tx.scan("user:", "user;")
        self.assertEqual(len(rows), 5)
        self.store.put("user:9", 9)      # phantom insert
        with self.assertRaises(PhantomConflict):
            tx.commit()

    def test_delete_from_scanned_range_aborts(self):
        tx = __import__("occ").Transaction(self.store)
        tx.scan("user:", "user;")

        def body(t):
            t.delete("user:2")
        self.store.run(body)             # phantom delete
        with self.assertRaises(PhantomConflict):
            tx.commit()

    def test_insert_outside_range_is_fine(self):
        tx = __import__("occ").Transaction(self.store)
        tx.scan("user:", "user;")
        self.store.put("zzz:other", 1)   # outside [user:, user;)
        tx.commit()                      # must succeed

    def test_update_of_key_in_range_is_fine_for_scan(self):
        # A scan records the key *set*; updating a value of a key already in
        # the range is not a phantom.  (It would conflict only if the txn
        # also *read* that key.)
        tx = __import__("occ").Transaction(self.store)
        tx.scan("user:", "user;")
        self.store.put("user:3", 333)
        tx.commit()

    def test_phantom_retry_eventually_commits(self):
        # run() must transparently retry a phantom conflict.
        store = self.store
        calls = []

        def body(tx):
            calls.append(1)
            rows = tx.scan("user:", "user;")
            if len(calls) == 1:
                store.put("user:7", 7)   # sabotage first attempt only
            return len(rows)

        self.assertEqual(store.run(body), 6)


class TestConcurrency(unittest.TestCase):
    def test_disjoint_keys_no_conflicts(self):
        store = OCCStore()
        threads = []
        for tid in range(8):
            def work(tid=tid):
                for i in range(100):
                    store.run(lambda tx: increment(tx, f"k{tid}"))
            threads.append(threading.Thread(target=work))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stats = store.stats.snapshot()
        self.assertEqual(stats["conflicts"], 0)
        self.assertEqual(stats["commits"], 800)
        for tid in range(8):
            self.assertEqual(store.get(f"k{tid}"), 100)

    def test_hotspot_all_increments_survive(self):
        store = OCCStore()
        n_threads, n_each = 6, 50

        def work():
            for _ in range(n_each):
                def body(tx):
                    value = tx.get("hot", 0) or 0
                    time.sleep(0.0002)   # widen the conflict window
                    tx.put("hot", value + 1)
                store.run(body)

        threads = [threading.Thread(target=work) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # No increment lost despite heavy conflicts.
        self.assertEqual(store.get("hot"), n_threads * n_each)
        self.assertGreater(store.stats.snapshot()["conflicts"], 0)


class TestFairness(unittest.TestCase):
    def test_long_transaction_is_not_starved(self):
        """A slow txn on a hot key must commit within a bounded time/retries."""
        store = OCCStore()
        stop = threading.Event()

        def hammer():
            while not stop.is_set():
                store.run(lambda tx: increment(tx, "hot"))

        hammers = [threading.Thread(target=hammer) for _ in range(4)]
        for t in hammers:
            t.start()
        try:
            before = store.stats.snapshot()
            start = time.monotonic()

            def long_body(tx):
                value = tx.get("hot", 0) or 0
                time.sleep(0.02)         # wide conflict window
                tx.put("long_marker", value)

            store.run(long_body, max_retries=100, senior_after=3)
            elapsed = time.monotonic() - start
            after = store.stats.snapshot()
        finally:
            stop.set()
            for t in hammers:
                t.join()

        self.assertIsNotNone(store.get("long_marker"))
        self.assertLess(elapsed, 5.0, "long txn took too long: starvation?")
        self.assertGreater(after["senior_commits"], before["senior_commits"])
        retries = after["retries"] - before["retries"]
        # senior_after bounds ordinary retries before the guaranteed commit.
        self.assertLessEqual(retries, 3 + 4 * 50)  # 3 own + hammers' noise

    def test_senior_queue_is_fifo(self):
        store = OCCStore()
        order = []
        done = threading.Event()

        def senior(tid):
            def body(tx):
                current = tx.get("order", []) or []
                tx.put("order", current + [tid])
            store._run_senior(body)
            order.append(tid)
            if len(order) == 3:
                done.set()

        threads = []
        for tid in range(3):
            t = threading.Thread(target=senior, args=(tid,))
            t.start()
            time.sleep(0.05)             # stagger arrivals deterministically
            threads.append(t)
        for t in threads:
            t.join()
        self.assertEqual(store.get("order"), [0, 1, 2])

    def test_senior_blocks_ordinary_commits(self):
        store = OCCStore()
        entered = threading.Event()
        release = threading.Event()
        committed = threading.Event()

        def senior_body(tx):
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            tx.put("senior", 1)

        senior = threading.Thread(target=lambda: store._run_senior(senior_body))
        senior.start()
        self.assertTrue(entered.wait(timeout=5))

        ordinary = threading.Thread(
            target=lambda: (store.put("ordinary", 1), committed.set()))
        ordinary.start()
        time.sleep(0.2)
        self.assertFalse(committed.is_set(),
                         "ordinary commit overtook a queued senior")
        release.set()
        senior.join(timeout=5)
        ordinary.join(timeout=5)
        self.assertTrue(committed.is_set())


class TestRetryExhaustion(unittest.TestCase):
    def test_retry_exhausted_raises(self):
        store = OCCStore()
        stop = threading.Event()

        def hammer():
            while not stop.is_set():
                store.run(lambda tx: increment(tx, "hot"))

        t = threading.Thread(target=hammer)
        t.start()
        try:
            with self.assertRaises(RetryExhausted) as ctx:
                def body(tx):
                    value = tx.get("hot", 0) or 0
                    time.sleep(0.01)     # hammer commits land in this window
                    tx.put("hot", value + 1)
                store.run(body, max_retries=2,
                          senior_after=10 ** 9)  # fairness disabled -> exhausts
            self.assertEqual(ctx.exception.attempts, 3)
        finally:
            stop.set()
            t.join()

    def test_zero_retries_fails_fast(self):
        store = OCCStore()
        store.put("x", 0)

        def body(tx):
            tx.get("x")
            store.put("x", 1)            # guarantee a conflict on attempt 1
            tx.put("y", 1)

        with self.assertRaises(RetryExhausted):
            store.run(body, max_retries=0, senior_after=10 ** 9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
