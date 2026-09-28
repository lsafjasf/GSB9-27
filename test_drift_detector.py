"""Self-tests for drift_detector (stdlib unittest)."""

import random
import unittest

from drift_detector import (
    CategoricalDriftDetector,
    EmptyBaselineError,
    InsufficientDataError,
    NumericDriftDetector,
    chi2_sf,
    g_test_pvalue,
    ks_pvalue,
    ks_statistic,
    psi,
)


def gauss(n, mu=0.0, sigma=1.0, seed=0):
    rng = random.Random(seed)
    return [rng.gauss(mu, sigma) for _ in range(n)]


class TestMetricPrimitives(unittest.TestCase):
    def test_psi_identical_is_zero(self):
        p = [0.2, 0.3, 0.5]
        self.assertAlmostEqual(psi(p, p), 0.0, places=9)

    def test_psi_symmetric_and_positive(self):
        a = psi([0.5, 0.5], [0.9, 0.1])
        b = psi([0.9, 0.1], [0.5, 0.5])
        self.assertAlmostEqual(a, b, places=9)
        self.assertGreater(a, 0)

    def test_ks_identical_samples(self):
        x = gauss(200, seed=1)
        self.assertAlmostEqual(ks_statistic(x, x), 0.0)

    def test_ks_bounds(self):
        d = ks_statistic(gauss(100, seed=2), gauss(100, mu=5, seed=3))
        self.assertGreater(d, 0.9)
        self.assertLessEqual(d, 1.0)

    def test_ks_pvalue_null_high(self):
        p = ks_pvalue(ks_statistic(gauss(300, seed=4), gauss(300, seed=5)), 300, 300)
        self.assertGreater(p, 0.05)

    def test_ks_pvalue_shift_low(self):
        p = ks_pvalue(ks_statistic(gauss(300, seed=6), gauss(300, mu=1, seed=7)), 300, 300)
        self.assertLess(p, 1e-6)

    def test_g_test_proportional_counts(self):
        g, p = g_test_pvalue([1 / 3, 2 / 3], [10, 20])
        self.assertAlmostEqual(g, 0.0, places=9)
        self.assertAlmostEqual(p, 1.0, places=6)

    def test_g_test_detects_skew(self):
        _, p = g_test_pvalue([0.5, 0.5], [90, 10])
        self.assertLess(p, 1e-6)

    def test_chi2_sf_known_values(self):
        # chi2 df=1: sf(3.841) ~= 0.05, sf(6.635) ~= 0.01
        self.assertAlmostEqual(chi2_sf(3.841, 1), 0.05, places=3)
        self.assertAlmostEqual(chi2_sf(6.635, 1), 0.01, places=3)


class TestNumericDetector(unittest.TestCase):
    def test_bin_edges_frozen(self):
        det = NumericDriftDetector().fit(gauss(500, seed=10))
        edges_before = det.bin_edges
        det.score(gauss(500, mu=100.0, seed=11))  # wild new data
        self.assertEqual(edges_before, det.bin_edges)
        self.assertEqual(len(det.bin_edges), 9)  # 10 bins -> 9 interior edges

    def test_no_drift_no_alert(self):
        det = NumericDriftDetector().fit(gauss(500, seed=12))
        rep = det.score(gauss(500, seed=13))
        self.assertFalse(rep.alert)
        self.assertEqual(rep.level, "ok")

    def test_shift_alerts(self):
        det = NumericDriftDetector().fit(gauss(500, seed=14))
        rep = det.score(gauss(500, mu=1.0, seed=15))
        self.assertTrue(rep.alert)

    def test_variance_change_alerts(self):
        det = NumericDriftDetector().fit(gauss(500, seed=16))
        rep = det.score(gauss(500, sigma=3.0, seed=17))
        self.assertTrue(rep.alert)

    def test_degenerate_constant_baseline(self):
        det = NumericDriftDetector(min_baseline=5).fit([1.0] * 50)
        self.assertEqual(det.bin_edges, [])  # single bin
        rep = det.score([1.0] * 50)
        self.assertFalse(rep.alert)
        rep2 = det.score([2.0] * 50)
        self.assertTrue(rep2.alert)

    def test_values_outside_baseline_range(self):
        det = NumericDriftDetector().fit(gauss(300, seed=18))
        rep = det.score(gauss(300, seed=19) + [-1e9, 1e9] * 10)
        self.assertIsNotNone(rep.psi)  # must not crash; falls into edge bins

    def test_empty_baseline_raises(self):
        with self.assertRaises(EmptyBaselineError):
            NumericDriftDetector().fit([])

    def test_tiny_baseline_raises(self):
        with self.assertRaises(InsufficientDataError):
            NumericDriftDetector().fit([1.0, 2.0, 3.0])

    def test_tiny_baseline_override(self):
        det = NumericDriftDetector(min_baseline=3, min_batch=3, n_bins=2)
        det.fit([1.0, 2.0, 3.0])
        rep = det.score([1.0, 2.0, 3.0])
        self.assertIn(rep.level, ("ok", "warn", "alert"))

    def test_empty_current_batch(self):
        det = NumericDriftDetector().fit(gauss(100, seed=20))
        rep = det.score([])
        self.assertEqual(rep.level, "no_data")
        self.assertFalse(rep.alert)

    def test_tiny_current_batch(self):
        det = NumericDriftDetector().fit(gauss(100, seed=21))
        rep = det.score([1.0, 2.0])
        self.assertEqual(rep.level, "insufficient_data")
        self.assertFalse(rep.alert)

    def test_score_before_fit_raises(self):
        with self.assertRaises(RuntimeError):
            NumericDriftDetector().score([1.0] * 50)


class TestCategoricalDetector(unittest.TestCase):
    def test_no_drift(self):
        rng = random.Random(30)
        base = [rng.choice("abc") for _ in range(500)]
        cur = [rng.choice("abc") for _ in range(500)]
        det = CategoricalDriftDetector().fit(base)
        self.assertFalse(det.score(cur).alert)

    def test_frequency_drift_alerts(self):
        rng = random.Random(31)
        base = [rng.choice("abc") for _ in range(500)]
        cur = ["a"] * 400 + ["b"] * 100
        det = CategoricalDriftDetector().fit(base)
        self.assertTrue(det.score(cur).alert)

    def test_unseen_category_folded_into_other(self):
        rng = random.Random(32)
        base = [rng.choice("abc") for _ in range(500)]
        det = CategoricalDriftDetector().fit(base)
        rep = det.score(["zzz"] * 200)
        self.assertTrue(rep.alert)  # mass moved to __OTHER__

    def test_empty_baseline_raises(self):
        with self.assertRaises(EmptyBaselineError):
            CategoricalDriftDetector().fit([])

    def test_tiny_current_batch(self):
        rng = random.Random(33)
        det = CategoricalDriftDetector().fit([rng.choice("ab") for _ in range(100)])
        self.assertEqual(det.score(["a", "b"]).level, "insufficient_data")

    def test_empty_current_batch(self):
        rng = random.Random(34)
        det = CategoricalDriftDetector().fit([rng.choice("ab") for _ in range(100)])
        self.assertEqual(det.score([]).level, "no_data")


if __name__ == "__main__":
    unittest.main(verbosity=2)
