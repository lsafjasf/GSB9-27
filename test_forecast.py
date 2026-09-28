"""Self-tests for forecast.py. Run: python3 -m unittest test_forecast -v"""

import math
import random
import unittest

from forecast import forecast, rolling_backtest


def trend_seasonal(n=96, seed=1):
    rng = random.Random(seed)
    return [60 + 0.4 * t + 8 * math.sin(2 * math.pi * t / 12) + rng.gauss(0, 2)
            for t in range(n)]


class TestDegenerateCases(unittest.TestCase):
    def test_constant_series(self):
        fc = forecast([42.0] * 30, horizon=5)
        self.assertEqual(fc.model, "constant")
        self.assertTrue(all(v == 42.0 for v in fc.values))
        self.assertTrue(all(lo == hi == 42.0 for lo, hi in zip(fc.lower, fc.upper)))
        self.assertTrue(any("constant" in w for w in fc.warnings))

    def test_single_observation(self):
        fc = forecast([7.5], horizon=3)
        self.assertEqual(fc.values, [7.5] * 3)
        self.assertTrue(fc.warnings)

    def test_short_series_mean_fallback(self):
        fc = forecast([1.0, 2.0, 4.0], horizon=4)
        self.assertEqual(fc.model, "mean")
        self.assertTrue(any("too short" in w for w in fc.warnings))
        self.assertTrue(all(fc.lower[h] < fc.values[h] < fc.upper[h]
                            for h in range(4)))

    def test_insufficient_seasonal_data_degrades(self):
        fc = forecast(trend_seasonal(n=18), horizon=4, season_length=12)
        self.assertFalse(fc.model.startswith("hw"))
        self.assertTrue(any("seasonal component dropped" in w
                            for w in fc.warnings))

    def test_empty_series_raises(self):
        with self.assertRaises(ValueError):
            forecast([], horizon=3)


class TestModelFit(unittest.TestCase):
    def test_linear_trend_recovered(self):
        y = [10.0 + 2.0 * t for t in range(60)]
        fc = forecast(y, horizon=5)
        for h in range(5):
            self.assertAlmostEqual(fc.values[h], 10.0 + 2.0 * (60 + h),
                                   delta=0.5)

    def test_seasonal_model_selected(self):
        fc = forecast(trend_seasonal(), horizon=12, season_length=12)
        self.assertTrue(fc.model.startswith("hw"),
                        f"expected Holt-Winters, got {fc.model}")
        self.assertIn("season_length", fc.params)

    def test_params_in_unit_interval(self):
        fc = forecast(trend_seasonal(), horizon=6, season_length=12)
        for key in ("alpha", "beta", "gamma"):
            self.assertGreaterEqual(fc.params[key], 0.0)
            self.assertLessEqual(fc.params[key], 1.0)

    def test_intervals_contain_point_and_widen(self):
        fc = forecast(trend_seasonal(), horizon=8, season_length=12)
        for h in range(8):
            self.assertLess(fc.lower[h], fc.values[h])
            self.assertGreater(fc.upper[h], fc.values[h])
        self.assertLessEqual(fc.sigma[0], fc.sigma[-1])

    def test_fixed_params_skip_search(self):
        fc = forecast(trend_seasonal(), horizon=4, season_length=12,
                      fixed_params=(0.3, 0.1, 0.1))
        self.assertEqual(fc.params["alpha"], 0.3)
        self.assertEqual(fc.params["beta"], 0.1)
        self.assertEqual(fc.params["gamma"], 0.1)


class TestStructuralChange(unittest.TestCase):
    def test_trend_break_warning(self):
        y = [20 + 0.3 * t for t in range(80)]
        y += [y[-1] + 1.5 * k for k in range(1, 21)]
        fc = forecast(y, horizon=5)
        self.assertTrue(any("structural change" in w for w in fc.warnings),
                        f"no structural-change warning: {fc.warnings}")

    def test_stable_series_no_warning(self):
        fc = forecast(trend_seasonal(), horizon=5, season_length=12)
        self.assertFalse(any("structural change" in w for w in fc.warnings))


class TestSeasonalKindFallback(unittest.TestCase):
    def _non_positive_series(self):
        y = trend_seasonal(n=48, seed=5)
        y[5] = 0.0  # non-positive value inside the first two seasons
        return y

    def test_explicit_mul_grid_falls_back_with_warning(self):
        y = self._non_positive_series()
        fc = forecast(y, horizon=6, season_length=12, seasonal="mul")
        self.assertNotEqual(fc.model, "hw-mul")
        self.assertTrue(any("fell back to additive" in w for w in fc.warnings))

    def test_explicit_mul_fixed_params_falls_back_with_warning(self):
        y = self._non_positive_series()
        fc = forecast(y, horizon=6, season_length=12, seasonal="mul",
                      fixed_params=(0.3, 0.1, 0.1))
        self.assertEqual(fc.model, "hw-add")
        self.assertTrue(any("non-positive values" in w for w in fc.warnings))

    def test_auto_silently_skips_infeasible_mul(self):
        y = self._non_positive_series()
        fc = forecast(y, horizon=6, season_length=12)  # seasonal="auto"
        self.assertNotEqual(fc.model, "hw-mul")
        self.assertFalse(any("fell back" in w for w in fc.warnings))

    def test_fixed_mul_honored_on_positive_data(self):
        y = trend_seasonal(n=60, seed=6)
        fc = forecast(y, horizon=4, season_length=12, seasonal="mul",
                      fixed_params=(0.5, 0.1, 0.2))
        self.assertEqual(fc.model, "hw-mul")
        self.assertEqual(fc.params["gamma"], 0.2)
        self.assertFalse(fc.warnings)

    def test_mul_infeasible_during_fit_falls_back(self):
        # First two seasons are strictly positive, so the upfront feasibility
        # check passes; an exact zero at t=2m drives a seasonal index to 0
        # under gamma=1, so _fit_hw returns None mid-fit (at t=3m).
        m = 12
        y = [10.0 + 0.1 * (i % m) for i in range(2 * m)]
        y += [0.0]
        y += [10.0 + 0.1 * ((i - (2 * m + 1)) % m)
              for i in range(2 * m + 1, 3 * m + 1)]
        self.assertEqual(len(y), 3 * m + 1)
        fc = forecast(y, horizon=4, season_length=m, seasonal="mul",
                      fixed_params=(0.5, 0.1, 1.0))
        self.assertEqual(fc.model, "hw-add")
        self.assertTrue(any("during fit" in w for w in fc.warnings))
        self.assertEqual(len(fc.values), 4)


class TestCoverage(unittest.TestCase):
    """Empirical interval coverage from a rolling-origin backtest should be
    close to the nominal 95% level on well-behaved data."""

    def test_coverage_near_confidence(self):
        y = trend_seasonal(n=96, seed=7)
        bt = rolling_backtest(y, horizon=6, season_length=12,
                              confidence=0.95, min_train=48, step=4)
        self.assertGreaterEqual(bt.n_origins, 10)
        self.assertGreaterEqual(bt.coverage, 0.80)
        self.assertLessEqual(bt.coverage, 1.0)

    def test_backtest_metrics_sane(self):
        y = trend_seasonal(n=96, seed=3)
        bt = rolling_backtest(y, horizon=6, season_length=12,
                              min_train=48, step=4)
        self.assertLess(bt.mae, bt.rmse * 1.5)
        self.assertLess(bt.mape, 15.0)
        self.assertEqual(len(bt.per_horizon), 6)


if __name__ == "__main__":
    unittest.main()
