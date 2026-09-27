"""Self-tests for sampler.py. Run: python3 test_sampler.py [-v]"""

import unittest

from sampler import Event, Sampler

DIMS = {"service": "api", "level": "info"}


def normal(i, ts=0.0, dims=DIMS, value=1.0):
    return Event(dims, message="metric-%d" % i, value=value, ts=ts)


class TestPriorityRules(unittest.TestCase):
    def test_errors_always_kept_under_burst(self):
        s = Sampler(sample_rate=0.01, max_per_combo_per_window=1)
        kept = [s.process(Event(DIMS, "boom", is_error=True, ts=i * 0.001))
                for i in range(1000)]
        self.assertTrue(all(kept))
        self.assertEqual(s.stats()["error"]["kept"], 1000)

    def test_spike_always_kept_even_when_rate_limited(self):
        s = Sampler(sample_rate=0.0 + 1e-9, spike_threshold=100.0,
                    max_per_combo_per_window=5)
        # Flood the combo so the rate limiter is saturated...
        for i in range(50):
            s.process(normal(i, ts=0.0))
        # ...a late spike must still be kept.
        self.assertTrue(s.process(normal(999, ts=0.0, value=10_000.0)))
        self.assertEqual(s.stats()["spike"]["kept"], 1)

    def test_normal_data_sampled_at_configured_rate(self):
        s = Sampler(sample_rate=0.1, max_per_combo_per_window=10 ** 9)
        n = 20_000
        kept = sum(s.process(normal(i, ts=0.0)) for i in range(n))
        self.assertTrue(0.07 < kept / n < 0.13, kept / n)
        st = s.stats()["normal_sampled"]
        self.assertAlmostEqual(st["effective_sample_rate"], kept / n)

    def test_sampling_is_deterministic(self):
        decisions = []
        for _ in range(2):
            s = Sampler(sample_rate=0.2, max_per_combo_per_window=10 ** 9)
            decisions.append([s.process(normal(i)) for i in range(500)])
        self.assertEqual(decisions[0], decisions[1])


class TestRateLimit(unittest.TestCase):
    def test_per_dimension_combo_limit(self):
        s = Sampler(sample_rate=1.0, max_per_combo_per_window=10)
        a = {"service": "a"}
        b = {"service": "b"}
        kept_a = sum(s.process(normal(i, dims=a)) for i in range(100))
        kept_b = sum(s.process(normal(i, dims=b)) for i in range(100))
        self.assertEqual(kept_a, 10)   # combo a capped independently
        self.assertEqual(kept_b, 10)   # combo b capped independently
        self.assertEqual(s.stats()["normal_rate_limited"]["dropped"], 180)

    def test_window_rollover_reopens_budget(self):
        s = Sampler(sample_rate=1.0, max_per_combo_per_window=5,
                    window_seconds=10.0)
        self.assertEqual(sum(s.process(normal(i, ts=0.0)) for i in range(10)), 5)
        self.assertEqual(sum(s.process(normal(i, ts=11.0)) for i in range(10)), 5)


class TestAllOverThreshold(unittest.TestCase):
    def test_everything_spiking_keeps_everything(self):
        s = Sampler(sample_rate=0.01, spike_threshold=50.0,
                    max_per_combo_per_window=1)
        kept = sum(s.process(normal(i, value=1e6)) for i in range(5000))
        self.assertEqual(kept, 5000)
        self.assertEqual(s.stats()["_total"]["dropped"], 0)


class TestDedupSuppression(unittest.TestCase):
    def test_first_kept_then_suppressed(self):
        s = Sampler(window_seconds=60.0)
        # First occurrence of each pattern must be kept.
        self.assertTrue(s.process(Event(DIMS, "disk full", dedup=True, ts=0.0)))
        self.assertTrue(s.process(Event(DIMS, "disk full", dedup=True, ts=0.0))
                        is False)
        self.assertFalse(s.process(Event(DIMS, "disk full", dedup=True, ts=1.0)))
        self.assertFalse(s.process(Event(DIMS, "disk full", dedup=True, ts=2.0)))
        # A *different* message is a new pattern: must not be masked.
        self.assertTrue(s.process(Event(DIMS, "oom killer", dedup=True, ts=3.0)))
        st = s.stats()
        self.assertEqual(st["new_pattern"]["kept"], 2)
        self.assertEqual(st["duplicate_suppressed"]["dropped"], 3)

    def test_new_window_reemits_pattern(self):
        s = Sampler(window_seconds=10.0)
        self.assertTrue(s.process(Event(DIMS, "flap", dedup=True, ts=0.0)))
        self.assertFalse(s.process(Event(DIMS, "flap", dedup=True, ts=5.0)))
        # Same pattern after the window: visible again (first of new window).
        self.assertTrue(s.process(Event(DIMS, "flap", dedup=True, ts=11.0)))

    def test_suppression_report_counts(self):
        s = Sampler(window_seconds=60.0)
        for i in range(10):
            s.process(Event(DIMS, "spam", dedup=True, ts=float(i)))
        report = s.suppression_report()
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["message"], "spam")
        self.assertEqual(report[0]["suppressed_count"], 9)


class TestCardinalityExplosion(unittest.TestCase):
    def test_pattern_table_bounded(self):
        s = Sampler(max_patterns=1000, window_seconds=10 ** 9)
        for i in range(50_000):  # 50k unique patterns >> max_patterns
            s.process(Event(DIMS, "unique-%d" % i, dedup=True, ts=0.0))
        self.assertLessEqual(len(s._patterns), 1000)
        self.assertGreater(s.stats()["pattern_evictions"]["dropped"], 0)
        # Every first occurrence was still kept (nothing silently lost).
        self.assertEqual(s.stats()["new_pattern"]["kept"], 50_000)


class TestNoData(unittest.TestCase):
    def test_empty(self):
        s = Sampler()
        st = s.stats()
        self.assertEqual(st["_total"]["kept"], 0)
        self.assertEqual(st["_total"]["dropped"], 0)
        self.assertIsNone(st["_total"]["effective_sample_rate"])
        self.assertEqual(s.suppression_report(), [])
        self.assertTrue(s.explain())


if __name__ == "__main__":
    unittest.main()
