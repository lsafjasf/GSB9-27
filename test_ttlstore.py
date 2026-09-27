"""Regression tests for TTLStore visibility guarantees.

Run: python3 test_ttlstore.py -v
"""

import random
import threading
import unittest

from ttlstore import TTLStore


class FakeClock:
    """Injectable, fully controllable clock."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt

    def rollback(self, dt):
        self.t -= dt


def assert_visibility(testcase, store, expected):
    """Visibility assertion: every read path must agree with `expected`
    (a {key: value} dict of live entries) and nothing else may leak."""
    for key, value in expected.items():
        testcase.assertEqual(store.get(key), value, f"get({key!r})")
    live_keys = set(expected)
    scanned = dict(store.scan())
    testcase.assertEqual(scanned, expected, "scan() leaked or dropped entries")
    testcase.assertEqual(store.count(), len(expected), "count() mismatch")
    iterated = dict(store.items())
    testcase.assertEqual(iterated, expected, "items() leaked or dropped entries")
    for key in list(store.__dict__.get("_data", {})):
        if key not in live_keys:
            testcase.assertIsNone(store.get(key), f"get({key!r}) returned stale data")


class TestPointLookup(unittest.TestCase):
    def test_get_hides_expired_without_sweep(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        store.put("a", 1, ttl=5)
        clock.advance(10)
        self.assertIsNone(store.get("a"))
        self.assertEqual(store.raw_size(), 1, "lazy deletion: slot still present")

    def test_get_returns_live_entries(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        store.put("a", 1, ttl=5)
        store.put("b", 2)  # no TTL
        clock.advance(4.9)
        self.assertEqual(store.get("a"), 1)
        self.assertEqual(store.get("b"), 2)


class TestRangeScan(unittest.TestCase):
    def test_scan_excludes_expired(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        store.put("k1", 1, ttl=5)
        store.put("k2", 2, ttl=100)
        store.put("k3", 3, ttl=5)
        clock.advance(10)
        self.assertEqual(store.scan(), [("k2", 2)])

    def test_scan_bounds_still_apply(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        for i in range(10):
            store.put(f"k{i}", i, ttl=5 if i % 2 else None)
        clock.advance(10)
        self.assertEqual(store.scan("k2", "k5"), [("k2", 2), ("k4", 4)])


class TestAggregation(unittest.TestCase):
    def test_count_excludes_expired(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        store.put("a", 1, ttl=5)
        store.put("b", 2, ttl=5)
        store.put("c", 3)
        clock.advance(10)
        self.assertEqual(store.count(), 1)


class TestIterator(unittest.TestCase):
    def test_items_excludes_expired(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        store.put("a", 1, ttl=5)
        store.put("b", 2)
        clock.advance(10)
        self.assertEqual(dict(store.items()), {"b": 2})

    def test_items_snapshot_consistent(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        store.put("a", 1, ttl=5)
        it = store.items()
        clock.advance(100)  # clock moves after snapshot, before consumption
        self.assertEqual(list(it), [("a", 1)])


class TestClockRollback(unittest.TestCase):
    def test_rollback_does_not_resurrect_expired(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        store.put("a", 1, ttl=5)
        clock.advance(10)      # now expired (monotonic now = 1010)
        self.assertIsNone(store.get("a"))  # anchor the high-water mark
        clock.rollback(8)      # wall clock back to 1002, before expires_at
        assert_visibility(self, store, {})

    def test_rollback_does_not_extend_visibility(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        store.put("a", 1, ttl=5)
        clock.advance(3)
        clock.rollback(2)  # wall clock says 1001, but monotonic now = 1003
        self.assertEqual(store.get("a"), 1)
        clock.advance(2)   # wall clock 1003 -> monotonic now still 1003
        self.assertEqual(store.get("a"), 1)
        clock.advance(3)   # monotonic now = 1006 > expiry
        self.assertIsNone(store.get("a"))


class TestWriteAlreadyExpired(unittest.TestCase):
    def test_zero_and_negative_ttl_never_visible(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        store.put("zero", 1, ttl=0)
        store.put("neg", 2, ttl=-10)
        assert_visibility(self, store, {})
        self.assertEqual(store.raw_size(), 2, "slots exist but must be invisible")

    def test_overwrite_expired_slot_revives_key(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        store.put("a", 1, ttl=0)
        store.put("a", 2, ttl=100)
        assert_visibility(self, store, {"a": 2})


class TestExplicitRenewal(unittest.TestCase):
    def test_touch_extends_lifetime(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        store.put("a", 1, ttl=5)
        clock.advance(4)
        self.assertTrue(store.touch("a", ttl=10))
        clock.advance(6)  # past original expiry, within renewed window
        assert_visibility(self, store, {"a": 1})

    def test_touch_on_expired_fails(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        store.put("a", 1, ttl=5)
        clock.advance(10)
        self.assertFalse(store.touch("a", ttl=10))
        assert_visibility(self, store, {})


class TestConcurrentDeleteAndRead(unittest.TestCase):
    def test_readers_never_see_expired_or_deleted(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        for i in range(200):
            store.put(f"k{i}", i, ttl=5 if i % 2 else None)
        clock.advance(10)  # half the keys are now expired
        errors = []

        def reader():
            try:
                for _ in range(200):
                    for k, v in store.scan():
                        idx = int(k[1:])
                        if idx % 2 or v != idx:
                            raise AssertionError(f"stale read: {k}={v}")
                    if store.count() > 100:
                        raise AssertionError("count leaked expired entries")
                    for k, v in store.items():
                        if int(k[1:]) % 2:
                            raise AssertionError(f"iterator leaked {k}")
            except AssertionError as exc:
                errors.append(exc)

        def deleter():
            for i in range(0, 200, 4):
                store.delete(f"k{i}")

        def sweeper():
            for _ in range(50):
                store.sweep()

        threads = ([threading.Thread(target=reader) for _ in range(4)]
                   + [threading.Thread(target=deleter)]
                   + [threading.Thread(target=sweeper)])
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class TestBatchScan(unittest.TestCase):
    def test_large_mixed_scan(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        expected = {}
        for i in range(5000):
            key = f"key{i:05d}"
            if i % 3 == 0:
                store.put(key, i, ttl=5)   # will expire
            elif i % 3 == 1:
                store.put(key, i, ttl=50)  # stays live
                expected[key] = i
            else:
                store.put(key, i)          # no TTL
                expected[key] = i
        clock.advance(10)
        assert_visibility(self, store, expected)
        self.assertEqual(len(store.scan()), len(expected))


class TestSweepDecoupling(unittest.TestCase):
    def test_reads_correct_when_sweep_never_runs(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        for i in range(100):
            store.put(f"e{i}", i, ttl=5)
        store.put("live", "x")
        clock.advance(100)
        assert_visibility(self, store, {"live": "x"})
        self.assertEqual(store.raw_size(), 101, "no sweep: garbage stays physical")

    def test_sweep_reclaims_but_does_not_change_visibility(self):
        clock = FakeClock()
        store = TTLStore(clock=clock)
        for i in range(100):
            store.put(f"e{i}", i, ttl=5)
        store.put("live", "x")
        clock.advance(100)
        before = dict(store.scan())
        removed = store.sweep()
        self.assertEqual(removed, 100)
        self.assertEqual(store.raw_size(), 1)
        self.assertEqual(dict(store.scan()), before)
        assert_visibility(self, store, {"live": "x"})


class TestVisibilityInvariantFuzz(unittest.TestCase):
    def test_random_ops_invariant(self):
        """After every random op, all read paths must agree and must never
        return an entry that is expired-and-not-renewed w.r.t. the
        monotonic clock."""
        rng = random.Random(42)
        clock = FakeClock()
        store = TTLStore(clock=clock)
        model = {}  # key -> (value, expires_at or None)
        mono_now = clock.t

        def expected():
            return {k: v for k, (v, exp) in model.items()
                    if exp is None or exp > mono_now}

        for step in range(3000):
            op = rng.random()
            key = f"k{rng.randrange(20)}"
            if op < 0.4:
                ttl = rng.choice([None, 0, -1, 5, 50])
                exp = None if ttl is None else mono_now + ttl
                value = rng.randrange(1000)
                store.put(key, value, ttl=ttl)
                model[key] = (value, exp)
            elif op < 0.55:
                store.delete(key)
                model.pop(key, None)
            elif op < 0.7:
                ttl = rng.choice([5, 50])
                if store.touch(key, ttl):
                    model[key] = (model[key][0], mono_now + ttl)
                else:
                    model.pop(key, None)
            elif op < 0.8:
                dt = rng.choice([1, 3, 10])
                clock.advance(dt)
                mono_now = max(mono_now, clock.t)
            elif op < 0.85:
                clock.rollback(rng.choice([1, 5]))
            elif op < 0.95:
                store.sweep()
                for k in [k for k, (_, e) in model.items()
                          if e is not None and e <= mono_now]:
                    del model[k]
            assert_visibility(self, store, expected())


if __name__ == "__main__":
    unittest.main()
