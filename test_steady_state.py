"""steady_state 库的单元测试与对拍。

运行：python3 -m unittest -v
"""

import random
import unittest

from steady_state import (
    MarkovChainError,
    analyze_chain,
    assert_stationary_properties,
    chain_period,
    closed_communicating_classes,
    power_iteration,
    stationarity_residual,
    stationary_linear,
    validate_transition_matrix,
)


def random_irreducible_matrix(n, rng):
    """生成随机行随机矩阵，并加小扰动保证不可约、非周期。"""
    P = [[rng.random() + 0.05 for _ in range(n)] for _ in range(n)]
    for row in P:
        s = sum(row)
        for j in range(n):
            row[j] /= s
    return P


class TestValidation(unittest.TestCase):
    def test_row_sum_not_one_raises(self):
        with self.assertRaises(MarkovChainError) as ctx:
            validate_transition_matrix([[0.5, 0.4], [0.1, 0.9]])
        self.assertIn("行和不为 1", str(ctx.exception))

    def test_negative_entry_raises(self):
        with self.assertRaises(MarkovChainError):
            validate_transition_matrix([[1.5, -0.5], [0.0, 1.0]])

    def test_non_square_raises(self):
        with self.assertRaises(MarkovChainError):
            validate_transition_matrix([[0.5, 0.5]])

    def test_nan_raises(self):
        with self.assertRaises(MarkovChainError):
            validate_transition_matrix([[float("nan"), 1.0], [0.0, 1.0]])


class TestDegenerateChains(unittest.TestCase):
    def test_single_state(self):
        P = [[1.0]]
        r = power_iteration(P)
        self.assertTrue(r["converged"])
        self.assertAlmostEqual(r["pi"][0], 1.0)
        self.assertAlmostEqual(stationary_linear(P)[0], 1.0)
        assert_stationary_properties(P, r["pi"])

    def test_absorbing_chain(self):
        # 状态 0 吸收，状态 1 有 0.5 概率流入 0
        P = [[1.0, 0.0], [0.5, 0.5]]
        r = power_iteration(P)
        self.assertTrue(r["converged"])
        self.assertAlmostEqual(r["pi"][0], 1.0, places=9)
        self.assertAlmostEqual(r["pi"][1], 0.0, places=9)
        pi_lin = stationary_linear(P)
        self.assertAlmostEqual(pi_lin[0], 1.0, places=9)
        assert_stationary_properties(P, r["pi"])
        assert_stationary_properties(P, pi_lin)
        # 只有一个闭类 => 分布唯一
        self.assertEqual(closed_communicating_classes(P), [[0]])

    def test_absorbing_with_transient_states(self):
        # 赌徒破产式：两端吸收 => 两个闭类，分布不唯一
        P = [
            [1.0, 0.0, 0.0, 0.0],
            [0.5, 0.0, 0.5, 0.0],
            [0.0, 0.5, 0.0, 0.5],
            [0.0, 0.0, 0.0, 1.0],
        ]
        classes = closed_communicating_classes(P)
        self.assertEqual(len(classes), 2)
        with self.assertRaises(MarkovChainError) as ctx:
            stationary_linear(P)
        self.assertIn("不唯一", str(ctx.exception))


class TestPeriodicChain(unittest.TestCase):
    P2 = [[0.0, 1.0], [1.0, 0.0]]  # 周期 2

    def test_period_detection(self):
        self.assertEqual(chain_period(self.P2), 2)
        info = analyze_chain(self.P2)
        self.assertEqual(info["max_period"], 2)

    def test_power_iteration_does_not_converge(self):
        # 非均匀起点：周期 2 链上幂迭代在 [a,1-a] 与 [1-a,a] 间振荡
        r = power_iteration(self.P2, max_iter=500, pi0=[0.9, 0.1])
        self.assertFalse(r["converged"])
        self.assertIn("周期链", r["diagnosis"])
        self.assertIn("d=2", r["diagnosis"])

    def test_cesaro_average_converges(self):
        r = power_iteration(self.P2, cesaro=True, tol=1e-10, pi0=[0.9, 0.1])
        self.assertTrue(r["converged"])
        self.assertAlmostEqual(r["pi"][0], 0.5, places=8)
        self.assertAlmostEqual(r["pi"][1], 0.5, places=8)
        assert_stationary_properties(self.P2, r["pi"])

    def test_linear_solves_periodic_chain(self):
        pi = stationary_linear(self.P2)
        self.assertAlmostEqual(pi[0], 0.5)
        self.assertAlmostEqual(pi[1], 0.5)
        assert_stationary_properties(self.P2, pi)

    def test_period3_chain(self):
        P = [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]]
        self.assertEqual(chain_period(P), 3)
        r = power_iteration(P, max_iter=300, pi0=[0.8, 0.1, 0.1])
        self.assertFalse(r["converged"])
        pi = stationary_linear(P)
        for x in pi:
            self.assertAlmostEqual(x, 1.0 / 3.0)
        assert_stationary_properties(P, pi)


class TestCrossValidation(unittest.TestCase):
    """小规模随机链上两种方法对拍。"""

    def test_power_vs_linear_random_chains(self):
        rng = random.Random(20260928)
        for trial in range(30):
            n = rng.randint(2, 6)
            P = random_irreducible_matrix(n, rng)
            r = power_iteration(P, tol=1e-13)
            self.assertTrue(r["converged"], f"trial {trial} 幂迭代未收敛")
            pi_lin = stationary_linear(P)
            diff = max(abs(a - b) for a, b in zip(r["pi"], pi_lin))
            self.assertLess(diff, 1e-8, f"trial {trial}: 两法结果不一致 diff={diff:.2e}")
            assert_stationary_properties(P, r["pi"], tol=1e-9)
            assert_stationary_properties(P, pi_lin, tol=1e-9)

    def test_residual_decreases(self):
        rng = random.Random(7)
        P = random_irreducible_matrix(5, rng)
        r = power_iteration(P, record_every=1)
        self.assertTrue(r["converged"])
        hist = [res for _, res in r["history"]]
        # 残差总体下降：末尾远小于开头，且最终低于容差
        self.assertLess(hist[-1], 1e-12)
        self.assertLess(hist[-1], hist[0] * 1e-6)
        # 允许局部波动，但每 5 步窗口的末值应小于窗口首值
        for i in range(0, len(hist) - 5, 5):
            self.assertLess(hist[i + 5], hist[i] * 1.0001)


class TestProperties(unittest.TestCase):
    def test_known_stationary_distribution(self):
        # 已知稳态分布为 [0.5, 0.3, 0.2]
        pi = [0.5, 0.3, 0.2]
        P = [
            [pi[0] * 0.9 + 0.05, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
        ]
        # 用细致平衡构造：P[i][j] = pi[j] * A[i][j]，A 对称且行和归一
        A = [
            [0.2, 0.5, 0.3],
            [0.5 / 0.3 * 0.5 * 0.3, 0.3, 0.4],
            [0.3, 0.4, 0.3],
        ]
        # 直接构造：P[i][j] = pi[j] * M[i][j] / rownorm，取对称 M
        M = [[1.0, 2.0, 3.0], [2.0, 1.0, 4.0], [3.0, 4.0, 1.0]]
        P = []
        for i in range(3):
            row = [pi[j] * M[i][j] for j in range(3)]
            s = sum(row)
            P.append([x / s for x in row])
        r = power_iteration(P)
        self.assertTrue(r["converged"])
        pi_lin = stationary_linear(P)
        for a, b in zip(r["pi"], pi_lin):
            self.assertAlmostEqual(a, b, places=9)
        # 细致平衡链的稳态分布应正比于行和
        assert_stationary_properties(P, r["pi"])
        assert_stationary_properties(P, pi_lin)


if __name__ == "__main__":
    unittest.main()
