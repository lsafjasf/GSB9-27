"""回归测试：错误/超阈值全量保留、无偏性、确定性可复现、边界场景。"""

from __future__ import annotations

import math
import random
import unittest
from typing import List

from legacy_sampler import LegacySampler
from sampler import (
    CATEGORIES,
    CATEGORY_ERROR,
    CATEGORY_NORMAL,
    CATEGORY_OVER_THRESHOLD,
    Record,
    Sampler,
)

RATE = 0.1
THRESHOLD = 1000.0


def make_records(n_normal: int, n_error: int, n_over: int, seed: int = 1) -> List[Record]:
    rng = random.Random(seed)
    records = []
    rid = 0
    for _ in range(n_normal):
        records.append(Record(rid, rng.uniform(0, 500), False))
        rid += 1
    for _ in range(n_error):
        records.append(Record(rid, rng.uniform(0, 500), True))
        rid += 1
    for _ in range(n_over):
        records.append(Record(rid, rng.uniform(THRESHOLD + 1, 5000), False))
        rid += 1
    rng.shuffle(records)
    return records


class SamplerCorrectnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sampler = Sampler(RATE, threshold_ms=THRESHOLD, seed=42)
        self.records = make_records(2000, 40, 10, seed=7)

    def test_errors_and_over_threshold_fully_retained(self):
        result = self.sampler.sample(self.records)
        self.assertEqual(result.stats[CATEGORY_ERROR].kept, 40)
        self.assertEqual(result.stats[CATEGORY_ERROR].dropped, 0)
        self.assertEqual(result.stats[CATEGORY_OVER_THRESHOLD].kept, 10)
        self.assertEqual(result.stats[CATEGORY_OVER_THRESHOLD].dropped, 0)
        kept_ids = result.kept_id_set()
        for record in self.records:
            if record.is_error or record.latency_ms > THRESHOLD:
                self.assertIn(record.record_id, kept_ids)

    def test_normal_sampled_near_rate_and_dropped_count_reported(self):
        result = self.sampler.sample(self.records)
        stat = result.stats[CATEGORY_NORMAL]
        self.assertEqual(stat.seen, 2000)
        self.assertEqual(stat.kept + stat.dropped, stat.seen)
        # 二项分布：期望保留 200 条，5 个标准差以外才算异常
        self.assertGreater(stat.kept, 200 - 5 * math.sqrt(2000 * RATE * (1 - RATE)))
        self.assertLess(stat.kept, 200 + 5 * math.sqrt(2000 * RATE * (1 - RATE)))
        self.assertEqual(stat.configured_rate, RATE)
        for cat in CATEGORIES:
            self.assertEqual(
                result.drop_counts()[cat], result.stats[cat].seen - result.stats[cat].kept
            )

    def test_reports_sample_rate_per_category(self):
        result = self.sampler.sample(self.records)
        rates = result.sample_rates()
        self.assertEqual(rates[CATEGORY_ERROR], 1.0)
        self.assertEqual(rates[CATEGORY_OVER_THRESHOLD], 1.0)
        self.assertEqual(rates[CATEGORY_NORMAL], RATE)
        self.assertEqual(result.stats[CATEGORY_NORMAL].realized_rate, stat_kept := result.stats[CATEGORY_NORMAL].kept / 2000)


class DeterminismTests(unittest.TestCase):
    def test_same_seed_same_sequence_identical_kept_set(self):
        records = make_records(3000, 50, 20, seed=3)
        s = Sampler(RATE, seed=12345)
        first = s.sample(records)
        second = s.sample(records)
        self.assertEqual(first.kept_id_set(), second.kept_id_set())
        self.assertEqual([r.record_id for r in first.kept], [r.record_id for r in second.kept])

    def test_explicit_seed_overrides_instance_seed(self):
        records = make_records(1000, 5, 2, seed=4)
        a = Sampler(RATE, seed=1).sample(records)
        b = Sampler(RATE, seed=1).sample(records, seed=99)
        c = Sampler(RATE, seed=1).sample(records, seed=99)
        self.assertNotEqual(a.kept_id_set(), b.kept_id_set())
        self.assertEqual(b.kept_id_set(), c.kept_id_set())

    def test_different_seed_usually_differs(self):
        records = make_records(5000, 0, 0, seed=5)
        sets = {Sampler(RATE, seed=s).sample(records).kept_id_set() for s in range(5)}
        self.assertEqual(len(sets), 5)

    def test_reordering_changes_decision_but_set_reproducible_per_sequence(self):
        records = make_records(1000, 0, 0, seed=6)
        reordered = list(reversed(records))
        a = Sampler(RATE, seed=77).sample(records)
        b = Sampler(RATE, seed=77).sample(reordered)
        c = Sampler(RATE, seed=77).sample(reordered)
        self.assertEqual(b.kept_id_set(), c.kept_id_set())
        # 决策绑定输入顺序：换序后保留集合一般不同（p=10%, n=1000，不同概率极高）
        self.assertNotEqual(a.kept_id_set(), b.kept_id_set())


class ScenarioTests(unittest.TestCase):
    def test_all_errors(self):
        records = make_records(0, 500, 0, seed=11)
        result = Sampler(RATE, seed=12).sample(records)
        self.assertEqual(len(result.kept), 500)
        self.assertEqual(result.drop_counts()[CATEGORY_ERROR], 0)
        self.assertEqual(result.estimated_count(), 500.0)

    def test_all_normal(self):
        records = make_records(5000, 0, 0, seed=13)
        result = Sampler(RATE, seed=14).sample(records)
        stat = result.stats[CATEGORY_NORMAL]
        self.assertEqual(stat.seen, 5000)
        self.assertEqual(result.stats[CATEGORY_ERROR].seen, 0)
        estimate = result.estimated_count(CATEGORY_NORMAL)
        self.assertAlmostEqual(estimate, stat.kept / RATE)
        self.assertLess(abs(estimate - 5000) / 5000, 0.05)

    def test_traffic_spike_ten_x(self):
        baseline = make_records(5000, 20, 8, seed=15)
        spike = make_records(50000, 200, 80, seed=15)
        rb = Sampler(RATE, seed=16).sample(baseline)
        rs = Sampler(RATE, seed=16).sample(spike)
        self.assertEqual(rs.stats[CATEGORY_ERROR].kept, 200)
        self.assertEqual(rs.stats[CATEGORY_OVER_THRESHOLD].kept, 80)
        self.assertEqual(rs.stats[CATEGORY_NORMAL].seen, 50000)
        # 流量 10 倍，异常现场同样 10 倍保留，错误一个不丢
        self.assertEqual(rs.stats[CATEGORY_ERROR].kept, rb.stats[CATEGORY_ERROR].kept * 10)
        self.assertEqual(rs.stats[CATEGORY_OVER_THRESHOLD].kept, rb.stats[CATEGORY_OVER_THRESHOLD].kept * 10)
        estimate = rs.estimated_count()
        self.assertLess(abs(estimate - 50280) / 50280, 0.02)

    def test_rate_zero_drops_all_normal_keeps_others(self):
        records = make_records(2000, 30, 10, seed=17)
        result = Sampler(0.0, seed=18).sample(records)
        self.assertEqual(result.stats[CATEGORY_NORMAL].kept, 0)
        self.assertEqual(result.stats[CATEGORY_NORMAL].dropped, 2000)
        self.assertEqual(result.stats[CATEGORY_ERROR].kept, 30)
        self.assertEqual(result.stats[CATEGORY_OVER_THRESHOLD].kept, 10)
        self.assertIsNone(result.estimated_count())
        self.assertIsNone(result.estimated_count(CATEGORY_NORMAL))
        # 只含全量类别时仍可估计
        self.assertEqual(result.estimated_count(CATEGORY_ERROR), 30.0)

    def test_rate_one_keeps_everything(self):
        records = make_records(1000, 5, 2, seed=19)
        result = Sampler(1.0, seed=20).sample(records)
        self.assertEqual(len(result.kept), len(records))
        self.assertEqual(sum(result.drop_counts().values()), 0)
        self.assertEqual(result.estimated_count(), float(len(records)))


class UnbiasednessTests(unittest.TestCase):
    def test_count_estimator_unbiased_monte_carlo(self):
        trials, true_n, rate = 300, 10000, RATE
        estimates = []
        for t in range(trials):
            records = make_records(true_n, 0, 0, seed=2000 + t)
            result = Sampler(rate, seed=30_000 + t).sample(records)
            estimates.append(result.estimated_count(CATEGORY_NORMAL))
        mean = sum(estimates) / trials
        self.assertAlmostEqual(mean, true_n, delta=true_n * 0.005)
        se_rel = math.sqrt((1 - rate) / (rate * true_n))
        # 均值标准误 = se_rel/sqrt(trials)；5 个标准差余量
        self.assertLess(abs(mean - true_n) / true_n, 5 * se_rel / math.sqrt(trials))

    def test_latency_sum_estimator_unbiased_monte_carlo(self):
        trials, true_n, rate = 300, 10000, RATE
        estimates = []
        for t in range(trials):
            records = make_records(true_n, 0, 0, seed=4000 + t)
            true_sum = sum(r.latency_ms for r in records)
            result = Sampler(rate, seed=50_000 + t).sample(records)
            estimates.append((result.estimated_latency_sum(CATEGORY_NORMAL), true_sum))
        rel_biases = [(hat - true) / true for hat, true in estimates]
        self.assertLess(abs(sum(rel_biases) / trials), 0.005)


class LegacyRegressionTests(unittest.TestCase):
    def test_legacy_dropped_errors_fixed_version_does_not(self):
        """回归：旧实现会在故障窗口丢掉错误样本，修复版必须全部保留。"""
        records = make_records(2000, 40, 10, seed=7)
        legacy_kept, _ = LegacySampler(RATE, seed=42).sample(records)
        legacy_errors = sum(1 for r in legacy_kept if r.is_error)
        fixed = Sampler(RATE, threshold_ms=THRESHOLD, seed=42).sample(records)
        self.assertEqual(fixed.stats[CATEGORY_ERROR].kept, 40)
        # 旧缺陷在该种子下确实复现（至少部分错误被丢弃）
        self.assertLess(legacy_errors, 40)


class ValidationTests(unittest.TestCase):
    def test_invalid_rate_rejected(self):
        with self.assertRaises(ValueError):
            Sampler(-0.1)
        with self.assertRaises(ValueError):
            Sampler(1.1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
