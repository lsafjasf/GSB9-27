"""EDD 调度的自测：穷举对拍 + 边界用例。运行: python3 test_edd.py"""

import itertools
import random
import unittest

from edd import Job, edd_order, lower_bound, max_lateness, schedule


def brute_force_optimal(jobs):
    """穷举所有排列，返回最小可能的最大超期。"""
    best = None
    for perm in itertools.permutations(jobs):
        t = 0
        lmax = None
        for j in perm:
            t += j.p
            lateness = t - j.d
            if lmax is None or lateness > lmax:
                lmax = lateness
        if best is None or lmax < best:
            best = lmax
    return best


class TestBruteForce(unittest.TestCase):
    """小规模随机任务集上，EDD 的最大超期必须等于穷举最优值。"""

    def check(self, jobs):
        got = max_lateness(schedule(jobs))
        want = brute_force_optimal(jobs)
        self.assertEqual(
            got, want, f"EDD={got} 但穷举最优={want}, jobs={jobs}"
        )

    def test_random_small(self):
        rng = random.Random(20260928)
        for n in range(1, 8):
            for _ in range(200):
                jobs = [
                    Job(i, rng.randint(1, 10), rng.randint(1, 20))
                    for i in range(n)
                ]
                self.check(jobs)

    def test_random_negative_due_and_ties(self):
        # 截止期允许为 0/负（已逾期任务），并大量制造并列截止期
        rng = random.Random(7)
        for n in range(1, 8):
            for _ in range(200):
                jobs = [
                    Job(i, rng.randint(1, 6), rng.choice([-3, 0, 5, 5, 5, 9]))
                    for i in range(n)
                ]
                self.check(jobs)


class TestEdgeCases(unittest.TestCase):
    def test_single_job(self):
        jobs = [Job(1, 5, 3)]
        pl = schedule(jobs)
        self.assertEqual(len(pl), 1)
        self.assertEqual((pl[0].start, pl[0].completion, pl[0].lateness), (0, 5, 2))
        self.assertEqual(max_lateness(pl), 2)
        lb, _ = lower_bound(jobs)
        self.assertEqual(lb, 2)  # 单任务时 Lmax 必等于下界

    def test_all_same_due_date(self):
        # 全部同时到期：任意顺序 Lmax 都等于 sum(p) - d
        jobs = [Job(i, p, 10) for i, p in enumerate([1, 4, 2, 3])]
        self.assertEqual(max_lateness(schedule(jobs)), 10 - 10)
        self.assertEqual(brute_force_optimal(jobs), 0)
        lb, _ = lower_bound(jobs)
        self.assertEqual(lb, 0)

    def test_extreme_processing_times(self):
        # 加工时间差异极大：1 与 10**9 共存
        jobs = [Job(1, 10**9, 10**9 + 1), Job(2, 1, 1), Job(3, 1, 2)]
        pl = schedule(jobs)
        # EDD 应先做两个短任务，长任务最后
        self.assertEqual([p.job.id for p in pl], [2, 3, 1])
        self.assertEqual(max_lateness(pl), (1 + 1 + 10**9) - (10**9 + 1))
        lb, _ = lower_bound(jobs)
        self.assertEqual(lb, max_lateness(pl))

    def test_infeasible_instance(self):
        # 不可行：总加工量超过最晚截止期，怎么排都必然超期
        jobs = [Job(1, 6, 4), Job(2, 6, 5), Job(3, 6, 6)]
        lmax = max_lateness(schedule(jobs))
        lb, reason = lower_bound(jobs)
        self.assertGreater(lb, 0, "下界应为正，表明必然超期")
        self.assertGreaterEqual(lmax, lb)
        self.assertEqual(lmax, 18 - 6)  # EDD: 1,2,3 -> C=6,12,18, 最大超期=18-6=12
        self.assertIn("sum(p) - max(d)", reason)

    def test_empty(self):
        self.assertEqual(schedule([]), [])
        self.assertEqual(max_lateness([]), 0)
        self.assertEqual(lower_bound([])[0], 0)

    def test_invalid_processing_time(self):
        with self.assertRaises(ValueError):
            Job(1, 0, 5)

    def test_edd_order_stability(self):
        jobs = [Job(3, 1, 5), Job(1, 1, 5), Job(2, 1, 2)]
        self.assertEqual([j.id for j in edd_order(jobs)], [2, 1, 3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
