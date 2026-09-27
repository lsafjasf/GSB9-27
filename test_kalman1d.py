"""Kalman1D 自测与对拍。

对拍参照:
1. reference_filter(): 手工推导的独立递推（普通形式 P=(1-K)P，非 Joseph），
   与库输出逐步比对，覆盖噪声突变、长时间缺失、初值远离真值。
2. 闭式解: q=0 时模型退化为常值，Kalman 终值应等于观测均值、方差等于 R/n
   （离线最小二乘解）。
"""

import math
import random
import unittest

from kalman1d import Kalman1D


def reference_filter(zs, x0, p0, q, rs):
    """手工推导的独立参照实现（普通协方差形式，无下限保护）。"""
    x, P = x0, p0
    out = []
    for k, z in enumerate(zs):
        P = P + q                      # 预测
        if z is None:
            out.append((x, P, None))
            continue
        R = rs[k]
        S = P + R
        K = P / S
        resid = z - x
        x = x + K * resid              # 更新
        P = (1.0 - K) * P
        out.append((x, P, resid))
    return out


def make_truth(n, drift=0.0, jump_at=None, jump=0.0, seed=1, q_true=0.01):
    rng = random.Random(seed)
    truth, x = [], 0.0
    for k in range(n):
        x += drift + rng.gauss(0.0, math.sqrt(q_true))
        if jump_at is not None and k == jump_at:
            x += jump
        truth.append(x)
    return truth


class TestAgainstReference(unittest.TestCase):
    """与手工推导参照序列逐步对拍。"""

    def _run_and_compare(self, zs, rs, x0, p0, q, r_default):
        # 对拍纯递推：关闭离群门限，离群逻辑另有专门用例
        kf = Kalman1D(x0=x0, p0=p0, q=q, r=r_default, gate=float("inf"))
        results = [kf.step(z, r=rs[k]) for k, z in enumerate(zs)]
        ref = reference_filter(zs, x0, p0, q, rs)
        for k, (res, (rx, rP, rresid)) in enumerate(zip(results, ref)):
            self.assertAlmostEqual(res.estimate, rx, places=9,
                                   msg=f"k={k} 估计不一致")
            self.assertAlmostEqual(res.variance, rP, places=9,
                                   msg=f"k={k} 方差不一致")
            if rresid is None:
                self.assertTrue(res.missing)
                self.assertIsNone(res.residual)
            else:
                self.assertAlmostEqual(res.residual, rresid, places=9,
                                       msg=f"k={k} 残差不一致")
        return results

    def test_noise_jump(self):
        """噪声突变: 观测噪声方差中途从 1.0 跳到 25.0。"""
        n = 400
        truth = make_truth(n, seed=7)
        rng = random.Random(11)
        rs = [1.0 if k < n // 2 else 25.0 for k in range(n)]
        zs = [t + rng.gauss(0, math.sqrt(rs[k])) for k, t in enumerate(truth)]
        results = self._run_and_compare(zs, rs, x0=0.0, p0=10.0, q=0.01,
                                        r_default=1.0)
        # 噪声变大后，单步更新对估计的拉动应变小（方差增长更快）
        self.assertGreater(results[-1].variance, results[n // 4].variance)

    def test_long_missing_gap(self):
        """长时间缺失: 中段 150 步无观测，仅预测。"""
        n = 400
        gap_start, gap_len = 150, 150
        truth = make_truth(n, seed=3)
        rng = random.Random(5)
        zs = [None if gap_start <= k < gap_start + gap_len
              else t + rng.gauss(0, 1.0) for k, t in enumerate(truth)]
        rs = [1.0] * n
        results = self._run_and_compare(zs, rs, x0=0.0, p0=1.0, q=0.01,
                                        r_default=1.0)
        # 缺测段: 估计冻结、方差每步严格增加 q
        p_before = results[gap_start - 1].variance
        for k in range(gap_start, gap_start + gap_len):
            self.assertTrue(results[k].missing)
            self.assertAlmostEqual(results[k].estimate,
                                   results[gap_start - 1].estimate, places=15)
            self.assertAlmostEqual(results[k].variance,
                                   p_before + (k - gap_start + 1) * 0.01,
                                   places=12)
        # 缺测结束后滤波恢复跟踪
        tail_err = abs(results[-1].estimate - truth[-1])
        self.assertLess(tail_err, 2.0)

    def test_far_initial_value(self):
        """初值远离真值: x0 偏差 1000，大 p0 下应快速收敛。"""
        n = 300
        truth = make_truth(n, seed=13)
        rng = random.Random(17)
        zs = [t + rng.gauss(0, 1.0) for t in truth]
        rs = [1.0] * n
        results = self._run_and_compare(zs, rs, x0=truth[0] + 1000.0,
                                        p0=1e6, q=0.01, r_default=1.0)
        err_50 = abs(results[50].estimate - truth[50])
        err_end = abs(results[-1].estimate - truth[-1])
        self.assertLess(err_50, 5.0)
        self.assertLess(err_end, 1.0)

    def test_closed_form_least_squares(self):
        """闭式对拍: q=0 时终值 = 观测均值, 方差 -> R/n（离线最小二乘解）。"""
        rng = random.Random(23)
        n = 500
        R = 4.0
        zs = [3.0 + rng.gauss(0, math.sqrt(R)) for _ in range(n)]
        kf = Kalman1D(x0=0.0, p0=1e9, q=0.0, r=R, gate=float("inf"))
        for z in zs:
            kf.step(z)
        mean = sum(zs) / n
        self.assertAlmostEqual(kf.x, mean, places=6)
        self.assertAlmostEqual(kf.P, R / n, delta=1e-3)


class TestOutlierRules(unittest.TestCase):
    """离群观测处理规则（可断言）。"""

    def _steady_filter(self, strategy):
        kf = Kalman1D(x0=0.0, p0=1.0, q=0.01, r=1.0,
                      gate=9.0, outlier_strategy=strategy)
        for _ in range(200):  # 进入稳态
            kf.step(0.1)
        return kf

    def test_inflate_downweights_outlier(self):
        """规则1(inflate): NIS>gate 时 R_eff=R*NIS/gate, 权重=gate/NIS<1。"""
        kf = self._steady_filter("inflate")
        x_before, P_before = kf.x, kf.P
        res = kf.step(50.0)  # 明显离群
        self.assertTrue(res.outlier)
        self.assertFalse(res.missing)
        self.assertLess(res.weight, 1.0)
        self.assertAlmostEqual(res.weight, 9.0 / res.nis, places=12)
        # 降权后估计移动量应远小于正常更新
        S = P_before + 0.01 + 1.0
        K_normal = (P_before + 0.01) / S
        self.assertLess(abs(res.estimate - x_before),
                        0.2 * abs(K_normal * 50.0))

    def test_reject_discards_outlier(self):
        """规则2(reject): NIS>gate 时整点拒绝, 权重=0, 估计与方差同纯预测。"""
        kf = self._steady_filter("reject")
        x_before, P_before = kf.x, kf.P
        res = kf.step(50.0)
        self.assertTrue(res.outlier)
        self.assertEqual(res.weight, 0.0)
        self.assertIsNone(res.residual)
        self.assertAlmostEqual(res.estimate, x_before, places=15)
        self.assertAlmostEqual(res.variance, P_before + 0.01, places=15)

    def test_normal_observation_untouched(self):
        """规则3: NIS<=gate 时正常更新, 权重=1, 不标离群。"""
        kf = self._steady_filter("inflate")
        res = kf.step(0.2)
        self.assertFalse(res.outlier)
        self.assertEqual(res.weight, 1.0)
        self.assertIsNotNone(res.residual)
        self.assertLessEqual(res.nis, 9.0)


class TestStability(unittest.TestCase):
    """数值稳定性。"""

    def test_variance_bounded_long_run(self):
        """长跑 2e6 步: 方差始终 >= p_min 且有限、非负、不塌缩为 0。"""
        kf = Kalman1D(x0=0.0, p0=1.0, q=1e-6, r=1e-6)
        rng = random.Random(29)
        for k in range(2_000_000):
            kf.step(rng.gauss(0, 1e-3))
            self.assertTrue(math.isfinite(kf.P))
            self.assertGreaterEqual(kf.P, kf.p_min)
        # 稳态方差应大于 0 且收敛到有界值
        self.assertGreater(kf.P, 0.0)
        self.assertLess(kf.P, 1.0)

    def test_variance_floor_with_zero_noise(self):
        """q->0 且 r 极小: 方差不得被舍入成 0 或负数。"""
        kf = Kalman1D(x0=0.0, p0=1.0, q=0.0, r=1e-14, p_min=1e-12)
        for _ in range(100_000):
            kf.step(0.0)
        self.assertGreaterEqual(kf.P, 1e-12)
        self.assertTrue(math.isfinite(kf.x))

    def test_huge_outlier_no_overflow(self):
        """极端离群值不导致溢出或非有限值。"""
        kf = Kalman1D(x0=0.0, p0=1.0, q=0.01, r=1.0)
        for _ in range(100):
            kf.step(0.0)
        res = kf.step(1e12)
        self.assertTrue(res.outlier)
        self.assertTrue(math.isfinite(kf.x))
        self.assertTrue(math.isfinite(kf.P))
        self.assertGreater(kf.P, 0.0)

    def test_missing_only_run(self):
        """全程缺测: 方差线性增长, 估计不变, 全部标注 missing。"""
        kf = Kalman1D(x0=5.0, p0=2.0, q=0.5, r=1.0)
        for k in range(1000):
            res = kf.step(None)
            self.assertTrue(res.missing)
            self.assertEqual(res.estimate, 5.0)
            self.assertAlmostEqual(res.variance, 2.0 + (k + 1) * 0.5, places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
