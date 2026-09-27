"""Tests for report_cache: first build, hot-key concurrency, frequent
dependency changes, and recompute-failure fallback. Stdlib unittest only."""

import threading
import time
import unittest

from report_cache import DataRegistry, ReportCache


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self.now

    def advance(self, dt):
        with self._lock:
            self.now += dt


def make_cache():
    clock = FakeClock()
    registry = DataRegistry(clock=clock)
    cache = ReportCache(registry, clock=clock)
    return clock, registry, cache


class FirstBuildTest(unittest.TestCase):
    def test_first_build_then_hit(self):
        clock, registry, cache = make_cache()
        calls = []
        cache.register("daily_sales", ["sales"], lambda: calls.append(1) or {"total": 42})

        self.assertEqual(cache.get("daily_sales"), {"total": 42})  # miss -> build
        self.assertEqual(cache.get("daily_sales"), {"total": 42})  # hit
        self.assertEqual(len(calls), 1)

        m = cache.metrics()
        self.assertEqual(m["hits"], 1)
        self.assertEqual(m["misses"], 1)
        self.assertAlmostEqual(m["hit_rate"], 0.5)
        self.assertEqual(m["recomputes"], 1)

    def test_separate_keys_are_independent(self):
        clock, registry, cache = make_cache()
        cache.register("r", ["d"], lambda: object())
        a = cache.get("r", key=("region", "cn"))
        b = cache.get("r", key=("region", "us"))
        self.assertIsNot(a, b)
        self.assertIs(cache.get("r", key=("region", "cn")), a)


class HotKeyConcurrencyTest(unittest.TestCase):
    def test_concurrent_misses_coalesce_into_one_recompute(self):
        clock, registry, cache = make_cache()
        compute_calls = []
        compute_lock = threading.Lock()
        entered = threading.Barrier(2)  # ensure a 2nd thread arrives mid-build

        def slow_compute():
            with compute_lock:
                compute_calls.append(1)
            if len(compute_calls) == 1:
                entered.wait(timeout=5)  # hold the flight open
            time.sleep(0.01)
            return {"report": "hot"}

        cache.register("hot", ["d"], slow_compute)

        n_threads = 16
        results = [None] * n_threads
        errors = []

        def worker(i):
            try:
                # give followers a chance to pile onto the in-flight entry
                if len(compute_calls) == 1:
                    try:
                        entered.wait(timeout=5)
                    except threading.BrokenBarrierError:
                        pass
                results[i] = cache.get("hot")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertTrue(all(r == {"report": "hot"} for r in results))
        # The whole storm produced exactly ONE recomputation.
        self.assertEqual(len(compute_calls), 1)
        self.assertEqual(cache.metrics()["recomputes"], 1)

    def test_no_thundering_herd_after_invalidation(self):
        """After a version bump, a concurrent burst still recomputes once."""
        clock, registry, cache = make_cache()
        compute_calls = []
        cache.register("hot", ["d"],
                       lambda: compute_calls.append(1) or len(compute_calls))
        cache.get("hot")
        registry.bump("d")

        results = []
        def worker():
            results.append(cache.get("hot"))
        threads = [threading.Thread(target=worker) for _ in range(32)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(compute_calls), 2)  # initial + one refresh
        self.assertTrue(all(r == 2 for r in results))


class FrequentDependencyChangeTest(unittest.TestCase):
    def test_only_affected_reports_refresh(self):
        clock, registry, cache = make_cache()
        a_calls, b_calls = [], []
        cache.register("report_a", ["orders"], lambda: a_calls.append(1) or "A")
        cache.register("report_b", ["users"], lambda: b_calls.append(1) or "B")

        cache.get("report_a")
        cache.get("report_b")
        self.assertEqual((len(a_calls), len(b_calls)), (1, 1))

        # orders changes 10 times; users never changes.
        for i in range(10):
            clock.advance(1.0)
            registry.bump("orders")
            cache.get("report_a")
            cache.get("report_b")  # must stay cached, no global flush

        self.assertEqual(len(a_calls), 11)   # rebuilt on every change
        self.assertEqual(len(b_calls), 1)    # never rebuilt
        m = cache.metrics()
        self.assertEqual(m["recomputes"], 12)

    def test_staleness_is_bounded_and_measured(self):
        clock, registry, cache = make_cache()
        cache.register("r", ["d"], lambda: "v")
        cache.get("r")

        clock.advance(3.0)
        registry.bump("d")          # data changes at t=1003
        clock.advance(0.5)
        cache.get("r")              # refreshed at t=1003.5 -> staleness 0.5

        m = cache.metrics()
        self.assertEqual(m["staleness_samples"], 1)
        self.assertAlmostEqual(m["avg_staleness"], 0.5)

    def test_unaffected_entry_survives_other_bumps(self):
        clock, registry, cache = make_cache()
        cache.register("r", ["d1"], lambda: {"n": 1})
        value = cache.get("r")
        for _ in range(5):
            registry.bump("d2")     # unrelated domain
            self.assertIs(cache.get("r"), value)


class RecomputeFailureTest(unittest.TestCase):
    def test_failure_keeps_old_value(self):
        clock, registry, cache = make_cache()
        state = {"fail": False}
        calls = []

        def compute():
            calls.append(1)
            if state["fail"]:
                raise RuntimeError("db down")
            return {"v": len(calls)}

        cache.register("r", ["d"], compute)
        self.assertEqual(cache.get("r"), {"v": 1})

        registry.bump("d")
        state["fail"] = True
        # Refresh fails -> old value is served instead of an exception.
        self.assertEqual(cache.get("r"), {"v": 1})
        m = cache.metrics()
        self.assertEqual(m["errors"], 1)
        self.assertEqual(m["stale_serves"], 1)

        # Data changes again, backend recovers -> fresh value.
        registry.bump("d")
        state["fail"] = False
        self.assertEqual(cache.get("r"), {"v": 3})

    def test_failure_without_old_value_raises_and_recovers(self):
        clock, registry, cache = make_cache()
        state = {"fail": True}

        def compute():
            if state["fail"]:
                raise RuntimeError("boom")
            return "ok"

        cache.register("r", ["d"], compute)
        with self.assertRaises(RuntimeError):
            cache.get("r")
        state["fail"] = False
        self.assertEqual(cache.get("r"), "ok")  # not stuck on the failure

    def test_concurrent_waiters_get_old_value_on_failure(self):
        clock, registry, cache = make_cache()
        cache.register("r", ["d"], lambda: "v1")
        cache.get("r")
        registry.bump("d")

        release = threading.Event()

        def failing_compute():
            release.wait(timeout=5)
            raise RuntimeError("db down")

        cache._reports["r"] = (("d",), failing_compute)
        results, errors = [], []

        def worker():
            try:
                results.append(cache.get("r"))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        time.sleep(0.05)
        release.set()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(results, ["v1"] * 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
