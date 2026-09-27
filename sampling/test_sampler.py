"""回归测试：分层采样器。"""

import subprocess
import sys
import unittest
from pathlib import Path

from sampler import (
    CATEGORY_ERROR,
    CATEGORY_NORMAL,
    CATEGORY_OVER_THRESHOLD,
    Record,
    Sampler,
    classify,
)

SEED = "test-seed"


def make_records(category, count, prefix="r"):
    return [Record(record_id=f"{prefix}-{i}", category=category)
            for i in range(count)]


class TestAlwaysKeep(unittest.TestCase):
    def test_all_errors_kept(self):
        """全是错误：必须 100% 保留，与采样率无关。"""
        records = make_records(CATEGORY_ERROR, 1_000)
        result = Sampler(seed=SEED, normal_rate=0.01).sample(records)
        stats = result.stats[CATEGORY_ERROR]
        self.assertEqual(stats.kept, 1_000)
        self.assertEqual(stats.dropped, 0)
        self.assertEqual(stats.effective_rate, 1.0)

    def test_over_threshold_kept(self):
        """超阈值记录同样全量保留。"""
        records = make_records(CATEGORY_OVER_THRESHOLD, 500)
        result = Sampler(seed=SEED, normal_rate=0.01).sample(records)
        self.assertEqual(result.stats[CATEGORY_OVER_THRESHOLD].kept, 500)

    def test_classify(self):
        self.assertEqual(classify(500, 10, 100), CATEGORY_ERROR)
        self.assertEqual(classify(200, 150, 100), CATEGORY_OVER_THRESHOLD)
        self.assertEqual(classify(200, 50, 100), CATEGORY_NORMAL)


class TestNormalSampling(unittest.TestCase):
    def test_all_normal_sampled_at_rate(self):
        """全正常：按比例采样，保留数在期望附近。"""
        records = make_records(CATEGORY_NORMAL, 20_000)
        result = Sampler(seed=SEED, normal_rate=0.1).sample(records)
        stats = result.stats[CATEGORY_NORMAL]
        self.assertEqual(stats.total, 20_000)
        self.assertEqual(stats.kept + stats.dropped, 20_000)
        # 期望 2000，std ~ 42.4，0.15 容差约为 17 倍标准差
        self.assertAlmostEqual(stats.effective_rate, 0.1, delta=0.015)

    def test_zero_rate_drops_all_normal_but_keeps_errors(self):
        """采样率为零：正常记录全丢弃，错误仍全保留。"""
        records = (make_records(CATEGORY_NORMAL, 1_000, "n")
                   + make_records(CATEGORY_ERROR, 20, "e"))
        result = Sampler(seed=SEED, normal_rate=0.0).sample(records)
        self.assertEqual(result.stats[CATEGORY_NORMAL].kept, 0)
        self.assertEqual(result.stats[CATEGORY_NORMAL].dropped, 1_000)
        self.assertEqual(result.stats[CATEGORY_ERROR].kept, 20)
        self.assertIsNone(result.normal_total_estimate())
        self.assertIsNone(result.normal_estimate_error())

    def test_invalid_rate_rejected(self):
        with self.assertRaises(ValueError):
            Sampler(seed=SEED, normal_rate=1.5)
        with self.assertRaises(ValueError):
            Sampler(seed=SEED, normal_rate=-0.1)


class TestTrafficSpike(unittest.TestCase):
    def test_tenfold_spike(self):
        """流量突增十倍：采样率稳定，错误在现场突增时仍全保留。"""
        baseline = (make_records(CATEGORY_NORMAL, 1_000, "base-n")
                    + make_records(CATEGORY_ERROR, 5, "base-e"))
        spike = (make_records(CATEGORY_NORMAL, 10_000, "spike-n")
                 + make_records(CATEGORY_ERROR, 200, "spike-e"))
        sampler = Sampler(seed=SEED, normal_rate=0.1)
        base_result = sampler.sample(baseline)
        spike_result = sampler.sample(spike)

        self.assertAlmostEqual(
            base_result.stats[CATEGORY_NORMAL].effective_rate, 0.1, delta=0.05)
        self.assertAlmostEqual(
            spike_result.stats[CATEGORY_NORMAL].effective_rate, 0.1, delta=0.02)
        self.assertEqual(spike_result.stats[CATEGORY_ERROR].kept, 200)
        self.assertEqual(base_result.stats[CATEGORY_ERROR].kept, 5)
        # 突增下总量估计仍无偏
        self.assertAlmostEqual(
            spike_result.normal_estimate_error(), 0.0, delta=0.03)


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_kept_set(self):
        """给定种子与输入序列，两次采样保留集合完全一致。"""
        records = make_records(CATEGORY_NORMAL, 5_000)
        kept1 = [r.record_id for r in
                 Sampler(seed=SEED, normal_rate=0.1).sample(records).kept_records]
        kept2 = [r.record_id for r in
                 Sampler(seed=SEED, normal_rate=0.1).sample(records).kept_records]
        self.assertEqual(kept1, kept2)

    def test_order_independent(self):
        """基于哈希的决策与输入顺序无关。"""
        records = make_records(CATEGORY_NORMAL, 5_000)
        kept_fwd = {r.record_id for r in
                    Sampler(seed=SEED, normal_rate=0.1).sample(records).kept_records}
        kept_rev = {r.record_id for r in
                    Sampler(seed=SEED, normal_rate=0.1)
                    .sample(list(reversed(records))).kept_records}
        self.assertEqual(kept_fwd, kept_rev)

    def test_different_seed_different_set(self):
        records = make_records(CATEGORY_NORMAL, 5_000)
        kept_a = {r.record_id for r in
                  Sampler(seed="a", normal_rate=0.1).sample(records).kept_records}
        kept_b = {r.record_id for r in
                  Sampler(seed="b", normal_rate=0.1).sample(records).kept_records}
        self.assertNotEqual(kept_a, kept_b)

    def test_reproducible_across_processes(self):
        """跨进程复现：子进程输出保留集合的指纹，必须一致。"""
        code = (
            "from sampler import Record, Sampler;"
            "import hashlib;"
            "rs=[Record(record_id=f'r-{i}',category='normal') for i in range(5000)];"
            "kept=[r.record_id for r in Sampler(seed='test-seed',normal_rate=0.1)"
            ".sample(rs).kept_records];"
            "print(hashlib.sha256(','.join(kept).encode()).hexdigest())"
        )
        outputs = []
        for _ in range(2):
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=Path(__file__).parent, capture_output=True, text=True,
                check=True)
            outputs.append(proc.stdout.strip())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(len(outputs[0]), 64)


class TestUnbiasedness(unittest.TestCase):
    def test_estimate_unbiased_across_seeds(self):
        """多种子下估计均值接近真实总量，单次误差有界。"""
        n = 100_000
        records = make_records(CATEGORY_NORMAL, n)
        errors = []
        for i in range(30):
            result = Sampler(seed=f"ub-{i}", normal_rate=0.1).sample(records)
            err = result.normal_estimate_error()
            errors.append(err)
            self.assertAlmostEqual(err, 0.0, delta=0.03)
        mean_err = sum(errors) / len(errors)
        self.assertAlmostEqual(mean_err, 0.0, delta=0.01)


if __name__ == "__main__":
    unittest.main(verbosity=2)
