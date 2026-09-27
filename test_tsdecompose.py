"""合成序列对拍：已知趋势与周期，验证重构误差与周期估计误差。"""

import math
import random
import unittest

from tsdecompose import decompose, estimate_period


def synth(n, period, trend_fn, amp=3.0, noise=0.3, seed=42, missing=0.0):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        v = trend_fn(i) + amp * math.sin(2.0 * math.pi * i / period)
        if noise:
            v += rng.gauss(0.0, noise)
        if missing and rng.random() < missing:
            out.append(None)
        else:
            out.append(v)
    return out


def rmse(pairs):
    errs = [a - b for a, b in pairs if a is not None and b is not None]
    if not errs:
        return float("nan")
    return math.sqrt(sum(e * e for e in errs) / len(errs))


def recon_rmse(dec, values):
    return rmse([(t + s + r, v)
                 for v, t, s, r in zip(values, dec.trend, dec.seasonal, dec.residual)
                 if v is not None])


LINEAR = lambda i: 0.05 * i + 1.0          # noqa: E731
JUMP = lambda i: 0.02 * i + (10.0 if i >= 250 else 0.0)  # noqa: E731


class TestPeriodEstimation(unittest.TestCase):
    def test_basic_period(self):
        self.assertEqual(estimate_period(synth(480, 24, LINEAR)), 24)

    def test_period_with_missing(self):
        self.assertEqual(estimate_period(synth(480, 24, LINEAR, missing=0.2)), 24)

    def test_period_not_dividing_length(self):
        self.assertEqual(estimate_period(synth(497, 24, LINEAR)), 24)

    def test_period_with_trend_jump(self):
        self.assertEqual(estimate_period(synth(500, 24, JUMP)), 24)

    def test_constant_series_has_no_period(self):
        self.assertIsNone(estimate_period([5.0] * 200))

    def test_pure_noise_usually_no_period(self):
        rng = random.Random(7)
        p = estimate_period([rng.gauss(0, 1) for _ in range(600)])
        self.assertIsNone(p)  # 阈值 0.3 对噪声足够保守

    def test_too_short_returns_none(self):
        self.assertIsNone(estimate_period([1.0, 2.0, 3.0]))


class TestDecomposition(unittest.TestCase):
    def test_full_mode_and_exact_reconstruction(self):
        values = synth(480, 24, LINEAR)
        dec = decompose(values)
        self.assertEqual(dec.mode, "full")
        self.assertEqual(dec.period, 24)
        self.assertLess(recon_rmse(dec, values), 1e-9)

    def test_component_recovery(self):
        n, p, amp = 480, 24, 3.0
        values = synth(n, p, LINEAR, amp=amp, noise=0.3)
        dec = decompose(values)
        true_seasonal = [amp * math.sin(2 * math.pi * i / p) for i in range(n)]
        true_trend = [LINEAR(i) for i in range(n)]
        s_err = rmse(list(zip(dec.seasonal, true_seasonal)))
        t_err = rmse(list(zip(dec.trend, true_trend)))
        self.assertLess(s_err, 0.5)   # 相对振幅 3.0 约 8% 以内
        self.assertLess(t_err, 0.5)

    def test_missing_points(self):
        values = synth(480, 24, LINEAR, missing=0.2)
        dec = decompose(values)
        self.assertEqual(dec.mode, "full")
        self.assertEqual(dec.period, 24)
        self.assertLess(recon_rmse(dec, values), 1e-9)
        for v, r in zip(values, dec.residual):  # 缺失处残差为 None，趋势/周期仍有定义
            if v is None:
                self.assertIsNone(r)
        self.assertTrue(all(t is not None for t in dec.trend))

    def test_period_not_dividing_length(self):
        values = synth(497, 24, LINEAR)
        dec = decompose(values)
        self.assertEqual(dec.mode, "full")
        self.assertEqual(dec.period, 24)
        self.assertLess(recon_rmse(dec, values), 1e-9)

    def test_trend_jump(self):
        n, p, amp = 500, 24, 3.0
        values = synth(n, p, JUMP, amp=amp, noise=0.2)
        dec = decompose(values)
        self.assertEqual(dec.mode, "full")
        self.assertEqual(dec.period, 24)
        self.assertLess(recon_rmse(dec, values), 1e-9)
        true_seasonal = [amp * math.sin(2 * math.pi * i / p) for i in range(n)]
        self.assertLess(rmse(list(zip(dec.seasonal, true_seasonal))), 0.8)

    def test_short_series_degrades(self):
        values = synth(30, 24, LINEAR)  # 不足两个周期
        dec = decompose(values)
        self.assertIn(dec.mode, ("short_for_period", "trend_only"))
        self.assertEqual(len(dec.trend), 30)
        self.assertLess(recon_rmse(dec, values), 1e-9)

    def test_very_short_series(self):
        dec = decompose([1.0, None, 3.0, 4.0])
        self.assertEqual(dec.mode, "insufficient_data")
        self.assertLess(recon_rmse(dec, [1.0, None, 3.0, 4.0]), 1e-9)

    def test_empty_and_all_missing(self):
        for series in ([], [None, None, None], [float("nan")] * 5):
            dec = decompose(series)
            self.assertEqual(dec.mode, "empty")
            self.assertIsNone(dec.period)

    def test_constant_series(self):
        values = [5.0] * 200
        dec = decompose(values)
        self.assertEqual(dec.mode, "trend_only")
        self.assertIsNone(dec.period)
        self.assertLess(recon_rmse(dec, values), 1e-9)
        self.assertLess(max(abs(r) for r in dec.residual), 1e-9)

    def test_pure_noise(self):
        rng = random.Random(11)
        values = [rng.gauss(0, 1) for _ in range(600)]
        dec = decompose(values)
        self.assertIn(dec.mode, ("trend_only", "full", "short_for_period"))
        self.assertLess(recon_rmse(dec, values), 1e-9)

    def test_nan_treated_as_missing(self):
        values = synth(240, 24, LINEAR)
        values[100] = float("nan")
        dec = decompose(values)
        self.assertEqual(dec.mode, "full")
        self.assertIsNone(dec.residual[100])


if __name__ == "__main__":
    unittest.main(verbosity=2)
