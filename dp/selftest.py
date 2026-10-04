"""差分隐私聚合库自测（标准库 unittest）。

覆盖情形：
  1. 单轮查询：基本有界误差性质、结果 != 真值（噪声确实存在）
  2. 连续多轮：预算在多轮间共享、逐步消耗、账本数据正确
  3. 小分组：低于阈值的分组被抑制，规则与抑制结果可审计
  4. 预算耗尽：剩余不足时抛 BudgetExhausted，且账本不新增记录
  5. 噪声/预算对应：scale = sensitivity/epsilon，epsilon 越小噪声越大
  6. 边界用例：epsilon 非法、空数据、阈值为 0、浮点不超支
"""

import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dp_lib import (
    BudgetExhausted,
    DPAggregator,
    PrivacyAccountant,
)


def make_data(n=1000, seed=1):
    rng = random.Random(seed)
    return [rng.randint(0, 100) for _ in range(n)]


class TestSingleQuery(unittest.TestCase):
    def test_count_is_noisy_but_close(self):
        acc = PrivacyAccountant(1.0, random.Random(42))
        agg = DPAggregator(acc)
        data = make_data(1000)
        res = agg.count(data, epsilon=1.0)
        self.assertEqual(res.true_value, 1000)
        self.assertNotAlmostEqual(res.value, 1000.0)  # 噪声确实加上了
        # Laplace(scale=1)：误差超过 10 的概率约 9e-5，1000 条数据下几乎不可能
        self.assertLess(abs(res.value - 1000), 10)
        self.assertAlmostEqual(res.noise_scale, 1.0)

    def test_sum_sensitivity_and_clamp(self):
        acc = PrivacyAccountant(0.5, random.Random(1))
        agg = DPAggregator(acc)
        data = [5, 5, 5, 500, -999]  # 含越界值，clamp 到 [0,10]
        res = agg.sum(data, epsilon=0.5, bound=10.0)
        self.assertAlmostEqual(res.true_value, 25.0)  # 5+5+5+10+0
        self.assertAlmostEqual(res.noise_scale, 2 * 10.0 / 0.5)  # 40
        self.assertAlmostEqual(acc.epsilon_remaining, 0.0)

    def test_mean(self):
        acc = PrivacyAccountant(1.0, random.Random(7))
        agg = DPAggregator(acc)
        res = agg.mean([1, 2, 3, 4], epsilon=1.0, bound=10.0, n=4)
        self.assertAlmostEqual(res.true_value, 2.5)
        self.assertAlmostEqual(res.noise_scale, 2 * 10.0 / 4)


class TestMultiRoundBudget(unittest.TestCase):
    def test_budget_consumed_step_by_step(self):
        acc = PrivacyAccountant(1.0, random.Random(3))
        agg = DPAggregator(acc)
        data = make_data(500)
        spends = [0.4, 0.3, 0.2]
        results = [agg.count(data, epsilon=e, name=f"count_r{i}")
                   for i, e in enumerate(spends)]

        # 每轮消耗与剩余预算
        for i, (res, e) in enumerate(zip(results, spends)):
            self.assertAlmostEqual(res.epsilon_used, e)
            self.assertAlmostEqual(res.round_no, i + 1)
        self.assertAlmostEqual(acc.epsilon_spent, 0.9)
        self.assertAlmostEqual(acc.epsilon_remaining, 0.1)
        self.assertEqual([e.round_no for e in acc.ledger], [1, 2, 3])
        self.assertEqual([e.epsilon_used for e in acc.ledger], spends)

    def test_noise_scale_tracks_budget(self):
        # 同一敏感度下，epsilon 越小 -> scale 越大
        acc = PrivacyAccountant(10.0, random.Random(0))
        agg = DPAggregator(acc)
        r_big_eps = agg.count([0] * 100, epsilon=5.0, name="big")
        r_small_eps = agg.count([0] * 100, epsilon=0.1, name="small")
        self.assertLess(r_big_eps.noise_scale, r_small_eps.noise_scale)
        self.assertAlmostEqual(r_big_eps.noise_scale, 1 / 5.0)
        self.assertAlmostEqual(r_small_eps.noise_scale, 1 / 0.1)


class TestSmallGroupSuppression(unittest.TestCase):
    def test_small_groups_suppressed(self):
        acc = PrivacyAccountant(1.0, random.Random(123))
        agg = DPAggregator(acc)
        # 组大小：90, 8, 2 —— 后两组低于阈值 10
        records = (["A"] * 90) + (["B"] * 8) + (["C"] * 2)
        res = agg.group_counts(records, key_fn=lambda x: x,
                               epsilon=1.0, threshold=10.0)
        self.assertIn("A", res.published_groups())
        self.assertNotIn("B", res.published_groups())
        self.assertNotIn("C", res.published_groups())
        self.assertEqual(res.suppressed, ["B", "C"])
        self.assertEqual(res.suppression_threshold, 10.0)
        # 审计信息完整：真实计数保留在结果对象中供核对，不对外发布
        self.assertEqual(res.true_counts, {"A": 90, "B": 8, "C": 2})

    def test_threshold_zero_publishes_nonnegative_noisy(self):
        acc = PrivacyAccountant(1.0, random.Random(5))
        agg = DPAggregator(acc)
        records = ["x"] * 5
        res = agg.group_counts(records, key_fn=lambda x: x,
                               epsilon=1.0, threshold=0.0)
        # noisy_count < 0 的组被抑制；单组大计数几乎必然保留
        self.assertEqual(res.published_groups(), ["x"])
        self.assertEqual(res.suppressed, [])


class TestBudgetExhaustion(unittest.TestCase):
    def test_exhausted_blocks_query_and_keeps_ledger(self):
        acc = PrivacyAccountant(0.5, random.Random(9))
        agg = DPAggregator(acc)
        agg.count([1, 2, 3], epsilon=0.3, name="q1")
        with self.assertRaises(BudgetExhausted):
            agg.count([1, 2, 3], epsilon=0.4, name="q2")  # 只剩 0.2
        # 被拒绝的查询不产生消耗，账本仍可继续使用 <= 剩余预算的查询
        self.assertAlmostEqual(acc.epsilon_spent, 0.3)
        self.assertEqual(len(acc.ledger), 1)
        r3 = agg.count([1], epsilon=0.2, name="q3")
        self.assertAlmostEqual(acc.epsilon_remaining, 0.0)

    def test_exact_budget_boundary_ok_then_exhausted(self):
        acc = PrivacyAccountant(0.5, random.Random(2))
        agg = DPAggregator(acc)
        agg.count([1], epsilon=0.5)  # 恰好用完
        with self.assertRaises(BudgetExhausted):
            agg.count([1], epsilon=0.0001)

    def test_group_query_also_respects_budget(self):
        acc = PrivacyAccountant(0.2, random.Random(4))
        agg = DPAggregator(acc)
        with self.assertRaises(BudgetExhausted):
            agg.group_counts(["a", "b"], key_fn=str, epsilon=0.3, threshold=5)
        self.assertEqual(acc.ledger, [])


class TestEdgeCases(unittest.TestCase):
    def test_invalid_epsilon(self):
        with self.assertRaises(ValueError):
            PrivacyAccountant(0.0)
        with self.assertRaises(ValueError):
            PrivacyAccountant(-1.0)
        acc = PrivacyAccountant(1.0)
        agg = DPAggregator(acc)
        with self.assertRaises(ValueError):
            agg.count([1], epsilon=0.0)
        with self.assertRaises(ValueError):
            agg.count([1], epsilon=-0.1)

    def test_empty_records(self):
        acc = PrivacyAccountant(2.0, random.Random(0))
        agg = DPAggregator(acc)
        r = agg.count([], epsilon=1.0)
        self.assertEqual(r.true_value, 0)
        self.assertEqual(agg.sum([], epsilon=0.5, bound=10.0).true_value, 0.0)
        g = agg.group_counts([], key_fn=lambda x: x, epsilon=0.5, threshold=1.0)
        self.assertEqual(g.published_groups(), [])
        self.assertEqual(g.suppressed, [])

    def test_float_tolerance_does_not_overspend(self):
        acc = PrivacyAccountant(0.3, random.Random(0))
        agg = DPAggregator(acc)
        agg.count([1], epsilon=0.1)
        agg.count([1], epsilon=0.1)
        agg.count([1], epsilon=0.1)
        self.assertGreaterEqual(acc.epsilon_remaining, 0.0)
        self.assertLessEqual(acc.epsilon_spent, acc.epsilon_total + 1e-9)

    def test_laplace_distribution_shape(self):
        # 经验分布检验：scale=1 时均值≈0，且 60%+ 样本落在 [-1,1]
        acc = PrivacyAccountant(1.0, random.Random(2024))
        agg = DPAggregator(acc)
        samples = [agg._laplace_noise(1.0) for _ in range(20000)]
        self.assertAlmostEqual(sum(samples) / len(samples), 0.0, delta=0.05)
        inside = sum(1 for s in samples if abs(s) <= 1.0) / len(samples)
        expected = 1.0 - math.exp(-1.0)
        self.assertAlmostEqual(inside, expected, delta=0.02)


if __name__ == "__main__":
    unittest.main(verbosity=2)
