"""smooth_forecast 自测：python3 -m unittest discover -s tests -v"""
import math
import random
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from smooth_forecast import Forecaster, rolling_backtest, Params
from smooth_forecast.stats import normal_quantile, mean, stdev
from smooth_forecast.models import (init_state, forecast_mean, update,
                                    variance_multipliers)
from smooth_forecast.fit import fit_params, select_model, one_step_sse


def make_series(n=200, trend=0.05, period=7, seasonal=8.0, noise=2.0,
                level=100.0, seed=42, shift_at=None, shift=0.0):
    rng = random.Random(seed)
    y = []
    for t in range(n):
        v = level + trend * t + seasonal * math.sin(2 * math.pi * t / period)
        if shift_at is not None and t >= shift_at:
            v += shift
        y.append(v + rng.gauss(0, noise))
    return y


class TestStats(unittest.TestCase):
    def test_normal_quantile(self):
        self.assertAlmostEqual(normal_quantile(0.975), 1.959964, places=5)
        self.assertAlmostEqual(normal_quantile(0.5), 0.0, places=10)
        self.assertAlmostEqual(normal_quantile(0.025), -1.959964, places=5)
        self.assertAlmostEqual(normal_quantile(0.995), 2.575829, places=5)

    def test_mean_stdev(self):
        self.assertEqual(mean([1, 2, 3]), 2.0)
        self.assertAlmostEqual(stdev([2, 4, 4, 4, 5, 5, 7, 9]), 2.138, places=3)


class TestModels(unittest.TestCase):
    def test_ses_converges_to_level(self):
        rng = random.Random(0)
        y = [50 + rng.gauss(0, 1) for _ in range(100)]
        fc = Forecaster(model="ses").fit(y)
        res = fc.predict(5)
        self.assertTrue(all(abs(p - 50) < 1.0 for p in res.point))

    def test_holt_recovers_trend(self):
        rng = random.Random(1)
        y = [10 + 0.5 * t + rng.gauss(0, 0.5) for t in range(120)]
        fc = Forecaster(model="holt").fit(y)
        res = fc.predict(4)
        # 末端水平约 10+0.5*119=69.5，4 步预测约 69.5+2=71.5
        self.assertAlmostEqual(res.point[-1], 71.5, delta=1.5)

    def test_seasonal_beats_nonseasonal(self):
        y = make_series()
        sse_hw, _, _, _ = one_step_sse(y, fit_params(y, "hw_add", 7)[0], 7)
        sse_holt, _, _, _ = one_step_sse(y, fit_params(y, "holt", 1)[0], 1)
        self.assertLess(sse_hw, sse_holt * 0.5)

    def test_variance_multiplier_grows(self):
        y = make_series()
        params, _ = fit_params(y, "hw_add", 7)
        state = init_state(y, "hw_add", 7)
        mult = variance_multipliers(state, params, 10)
        self.assertEqual(len(mult), 10)
        self.assertAlmostEqual(mult[0], 1.0, places=6)
        self.assertTrue(all(mult[i] <= mult[i + 1] + 1e-9 for i in range(9)))


class TestFitting(unittest.TestCase):
    def test_params_in_range(self):
        y = make_series()
        for model in ("ses", "holt", "holt_damped", "hw_add", "hw_mul"):
            params, sse = fit_params(y, model, 7 if model.startswith("hw") else 1)
            self.assertTrue(0 < params.alpha < 1)
            self.assertTrue(math.isfinite(sse))

    def test_auto_selects_seasonal(self):
        y = make_series()
        model, params, sse, n = select_model(y, 7)
        self.assertIn(model, ("hw_add", "hw_mul"))

    def test_fixed_params_skip_fit(self):
        y = make_series()
        p = Params("holt", alpha=0.3, beta=0.1)
        fc = Forecaster(params=p).fit(y)
        self.assertIs(fc._params, p)


class TestEdgeCases(unittest.TestCase):
    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            Forecaster().fit([])

    def test_short_series_fallback(self):
        fc = Forecaster().fit([3.0, 4.0, 5.0])
        res = fc.predict(3)
        self.assertEqual(res.model, "mean_fallback")
        self.assertTrue(any("过短" in w for w in res.warnings))
        self.assertTrue(all(abs(p - 4.0) < 1e-9 for p in res.point))
        self.assertTrue(all(u > l for l, u in zip(res.lower, res.upper)))

    def test_constant_series(self):
        fc = Forecaster().fit([7.5] * 60)
        res = fc.predict(5)
        self.assertTrue(any("常数" in w for w in res.warnings))
        self.assertTrue(all(abs(p - 7.5) < 1e-6 for p in res.point))
        self.assertTrue(all(u - l < 1e-3 for l, u in zip(res.lower, res.upper)))

    def test_trend_shift_warning(self):
        y = make_series(n=150, shift_at=140, shift=20.0)
        fc = Forecaster(period=7).fit(y)
        self.assertTrue(any("突变" in w for w in fc.warnings))

    def test_no_season_when_insufficient(self):
        y = make_series(n=10)
        fc = Forecaster(period=7, min_points=4).fit(y)
        self.assertTrue(any("季节" in w for w in fc.warnings))
        self.assertNotIn(fc._model, ("hw_add", "hw_mul"))

    def test_nonpositive_skips_multiplicative(self):
        rng = random.Random(2)
        y = [rng.gauss(0, 5) for _ in range(80)]
        fc = Forecaster(period=7).fit(y)
        self.assertNotEqual(fc._model, "hw_mul")


class TestBacktestCoverage(unittest.TestCase):
    def test_coverage_near_nominal(self):
        """95% 区间在滚动回测中的覆盖率应接近 0.95（宽松区间防随机抖动）。"""
        y = make_series(n=250, seed=7)
        reports = rolling_backtest(y, horizon=3, period=7, confidence=0.95,
                                   refit_every=25)
        rep = reports[0]
        self.assertGreaterEqual(rep.coverage, 0.85)
        self.assertLessEqual(rep.coverage, 1.0)
        self.assertEqual(rep.n_origins > 100, True)

    def test_multi_config(self):
        y = make_series(n=150, seed=3)
        reports = rolling_backtest(
            y, horizon=2, period=7,
            configs={"auto": {}, "ses": {"model": "ses"}},
            refit_every=30)
        self.assertEqual(len(reports), 2)
        self.assertLess(reports[0].mae, reports[1].mae)


if __name__ == "__main__":
    unittest.main()
