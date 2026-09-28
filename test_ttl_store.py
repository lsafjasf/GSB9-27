"""Regression tests for the expiry visibility fix (stdlib unittest only)."""

import random
import threading
import time
import unittest

from clock import FakeClock, SystemClock
from store_buggy import TTLStore as BuggyStore
from store_fixed import ExpiryJanitor, TTLStore


def make_store():
    clock = FakeClock()
    return TTLStore(clock), clock


def assert_invisible_if_expired(test, store, clock, key, value=1):
    """Visibility assertion reused across paths: at time >= deadline no path
    may expose the entry, at time < deadline every path must expose it."""
    deadline = clock.now()

    test.assertIsNone(store.get(key))
    test.assertNotIn(key, store)
    test.assertIsNone(store.ttl_of(key))
    test.assertEqual(store.get_many([key]), [None])
    test.assertEqual(list(store.scan()), [])
    test.assertEqual(list(store.keys()), [])
    test.assertEqual(list(store.values()), [])
    test.assertEqual(list(store.items()), [])
    test.assertNotIn(key, list(store))
    test.assertEqual(len(store), 0)
    test.assertEqual(store.count(), 0)
    test.assertEqual(store.sum("v"), 0)
    test.assertIsNone(store.avg("v"))
    test.assertIsNone(store.min("v"))
    test.assertIsNone(store.max("v"))

    clock.rollback(1)
    test.assertLess(clock.now(), deadline)
    test.assertIsNone(store.get(key))  # sticky: rollback cannot resurrect
    clock.advance(1)


class ExpiryVisibilityTests(unittest.TestCase):
    def test_point_get_expires_on_boundary(self):
        store, clock = make_store()
        store.put("k", "v", ttl=10)
        self.assertEqual(store.get("k"), "v")
        clock.advance(10)
        self.assertIsNone(store.get("k"))

    def test_scan_filters_expired_range_and_prefix(self):
        store, clock = make_store()
        store.put("a", {"v": 1}, ttl=10)
        store.put("b", {"v": 2}, ttl=20)
        store.put("c", {"v": 3}, ttl=None)
        clock.advance(15)
        self.assertEqual(list(store.scan()), [("b", {"v": 2}), ("c", {"v": 3})])
        self.assertEqual(list(store.scan(start="b", end="z")),
                         [("b", {"v": 2}), ("c", {"v": 3})])
        self.assertEqual(list(store.scan(start="c")), [("c", {"v": 3})])

    def test_aggregates_exclude_expired(self):
        store, clock = make_store()
        store.put("a", {"v": 1}, ttl=10)
        store.put("b", {"v": 2}, ttl=20)
        store.put("c", {"v": 9}, ttl=None)
        clock.advance(15)
        self.assertEqual(store.count(), 2)
        self.assertEqual(store.sum("v"), 11)
        self.assertEqual(store.avg("v"), 5.5)
        self.assertEqual(store.min("v"), 2)
        self.assertEqual(store.max("v"), 9)

    def test_iterators_hide_expired_mid_iteration(self):
        store, clock = make_store()
        for k in ("a", "b", "c"):
            store.put(k, k, ttl=10)
        it = iter(store.keys())
        self.assertEqual(next(it), "a")
        clock.advance(10)
        self.assertEqual(list(it), [])

    def test_clock_rollback_cannot_resurrect_observed_expiry(self):
        store, clock = make_store()
        store.put("k", 1, ttl=10)
        clock.advance(10)
        self.assertIsNone(store.get("k"))
        clock.rollback(50)
        self.assertIsNone(store.get("k"))
        self.assertEqual(list(store.scan()), [])
        self.assertEqual(store.count(), 0)

    def test_clock_rollback_before_observation_keeps_live(self):
        store, clock = make_store()
        store.put("k", 1, ttl=10)
        clock.advance(5)
        clock.rollback(5)
        self.assertEqual(store.get("k"), 1)

    def test_put_with_non_positive_ttl_is_expired_on_arrival(self):
        store, clock = make_store()
        store.put("zero", "z", ttl=0)
        store.put("neg", "n", ttl=-5)
        self.assertIsNone(store.get("zero"))
        self.assertIsNone(store.get("neg"))
        self.assertEqual(list(store.scan()), [])
        self.assertEqual(store.count(), 0)
        # physically present until cleanup runs, never visible
        self.assertEqual(store.physical_size(), 2)

    def test_correct_without_any_purge(self):
        store, clock = make_store()
        store.put("k", {"v": 1}, ttl=1)
        clock.advance(2)
        assert_invisible_if_expired(self, store, clock, "k", value={"v": 1})
        self.assertEqual(store.physical_size(), 1)
        self.assertEqual(store.purge_expired(), 1)
        self.assertEqual(store.physical_size(), 0)

    def test_janitor_cleansup_but_reads_already_correct(self):
        store, clock = make_store()
        store.put("k", 1, ttl=1)
        clock.advance(2)
        janitor = ExpiryJanitor(store, interval=0.01).start()
        self.addCleanup(janitor.stop)
        deadline = time.time() + 2
        while store.physical_size() and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(store.physical_size(), 0)
        self.assertEqual(store.count(), 0)

    def test_concurrent_delete_and_purge_against_readers(self):
        store = TTLStore(SystemClock())
        stop = threading.Event()
        errors = []

        def writer():
            i = 0
            while not stop.is_set():
                deadline = time.monotonic() + 0.05
                store.put(i % 50, {"deadline": deadline}, ttl=0.05)
                i += 1

        def reader():
            while not stop.is_set():
                now = time.monotonic()
                try:
                    for _, value in store.scan():
                        if now > value["deadline"]:
                            errors.append("expired row visible: %r" % value)
                    store.get(random.randrange(50))
                    store.get_many(range(50))
                    store.count()
                    list(store)
                except Exception as exc:  # structural races would surface here
                    errors.append(repr(exc))

        def cleaner():
            while not stop.is_set():
                store.purge_expired()

        threads = [threading.Thread(target=writer)]
        threads += [threading.Thread(target=reader) for _ in range(3)]
        threads.append(threading.Thread(target=cleaner))
        for t in threads:
            t.start()
        time.sleep(1.0)
        stop.set()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_batch_scan_many_expired_mixed(self):
        store, clock = make_store()
        for i in range(100):
            store.put("k%03d" % i, {"v": i}, ttl=(10 if i % 2 else 20))
        clock.advance(15)
        live = list(store.scan())
        self.assertEqual(len(live), 50)
        self.assertTrue(all(int(k[1:]) % 2 == 0 for k, _ in live))
        self.assertEqual(store.count(prefix="k00"), 5)  # k000,k002,...
        self.assertEqual(store.get_many(["k000", "k001", "missing"]),
                         [{"v": 0}, None, None])

    def test_explicit_renewal_clears_expiry(self):
        store, clock = make_store()
        store.put("k", 1, ttl=10)
        clock.advance(10)
        self.assertIsNone(store.get("k"))
        self.assertTrue(store.touch("k", 10))
        self.assertEqual(store.get("k"), 1)
        clock.advance(5)
        store.put("k", 2, ttl=10)
        clock.advance(8)
        self.assertEqual(store.get("k"), 2)

    def test_randomized_visibility_invariant(self):
        store, clock = make_store()
        deadlines = {}
        sticky = set()
        rng = random.Random(7)
        for _ in range(2000):
            op = rng.randrange(6)
            key = "k%d" % rng.randrange(20)
            if op == 0:
                ttl = rng.choice([0, 1, 5, 10, None])
                store.put(key, 1, ttl=ttl)
                deadlines[key] = None if ttl is None else clock.now() + ttl
                sticky.discard(key)
            elif op == 1:
                store.delete(key)
                deadlines.pop(key, None)
                sticky.discard(key)
            elif op == 2 and key in deadlines:
                store.touch(key, 7)
                deadlines[key] = clock.now() + 7
                sticky.discard(key)
            elif op in (3, 4):
                clock.advance(rng.randrange(3))
            else:
                clock.rollback(rng.randrange(3))
            now = clock.now()
            # the reads below observe expiry: mirror the sticky gate
            for k, d in deadlines.items():
                if d is not None and now >= d:
                    sticky.add(k)
            visible = {k for k, d in deadlines.items()
                       if k not in sticky and (d is None or d > now)}
            self.assertEqual(set(store.keys()), visible)
            self.assertEqual(store.count(), len(visible))


class BuggyBaselineTests(unittest.TestCase):
    """Characterize the broken behaviour the fix removes."""

    def test_buggy_leaks_expired_data_on_non_point_paths(self):
        clock = FakeClock()
        store = BuggyStore(clock)
        store.put("a", {"v": 1}, ttl=10)
        store.put("b", {"v": 2}, ttl=20)  # never touched via get()
        clock.advance(15)
        # a and b are both expired; only get() checks, every other path leaks:
        self.assertEqual(store.get_many(["a", "b"]), [{"v": 1}, {"v": 2}])
        self.assertIn("a", store)
        self.assertEqual(store.count(), 2)
        self.assertEqual([k for k, _ in store.scan()], ["a", "b"])
        self.assertEqual(len(store), 2)
        self.assertEqual(list(store), ["a", "b"])
        # put with ttl=0 is born live to non-get paths:
        store.put("z", 3, ttl=0)
        self.assertEqual(store.get_many(["z"]), [3])
        # expiry observed via a non-deleting path; rollback then "un-expires"
        # the still-present row (the fixed store keeps expiry sticky instead):
        self.assertEqual([k for k, _ in store.scan()], ["a", "b", "z"])
        clock.rollback(20)
        self.assertEqual(store.get("a"), {"v": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
