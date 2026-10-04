"""自适应采样库自测（unittest，仅标准库）。

运行：python3 -m unittest -v test_adaptive_sampler.py
"""

import unittest

from src.adaptive_sampler import AdaptiveSampler


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make_sampler(clock, budget=50.0, window=5.0, hard_cap=None):
    return AdaptiveSampler(
        budget_per_sec=budget,
        window_sec=window,
        min_sample_rate=0.0,
        hard_cap_per_sec=hard_cap,
        clock=clock,
    )


def feed(sampler, clock, rps, seconds, error_ratio=0.0, trace_prefix="t"):
    """以固定速率喂入链路，返回 (offered, kept, err_offered, err_kept)。"""
    offered = kept = err_offered = err_kept = 0
    step = 1.0 / rps
    n = int(rps * seconds)
    for i in range(n):
        is_error = error_ratio > 0 and (i % max(1, round(1 / error_ratio)) == 0)
        keep = sampler.should_sample(f"{trace_prefix}-{i}", is_error=is_error)
        offered += 1
        kept += keep
        err_offered += is_error
        err_kept += is_error and keep
        clock.advance(step)
    return offered, kept, err_offered, err_kept


class TestLowTraffic(unittest.TestCase):
    """低流量：远低于预算时采样率应升到 1，全部保留。"""

    def test_low_traffic_keeps_everything(self):
        clock = FakeClock()
        s = make_sampler(clock, budget=50.0)
        offered, kept, _, _ = feed(s, clock, rps=5, seconds=10)
        self.assertEqual(offered, kept)
        self.assertAlmostEqual(s.sample_rate(), 1.0)


class TestTrafficSpike(unittest.TestCase):
    """流量突增：采样率应随流量下降，保留量贴近预算。"""

    def test_spike_lowers_rate(self):
        clock = FakeClock()
        s = make_sampler(clock, budget=50.0, window=2.0)
        feed(s, clock, rps=10, seconds=5)          # 预热：低流量
        rate_before = s.sample_rate()
        feed(s, clock, rps=500, seconds=3, trace_prefix="spike-warm")  # 爬升段
        offered, kept, _, _ = feed(s, clock, rps=500, seconds=5, trace_prefix="spike")
        rate_after = s.sample_rate()
        self.assertAlmostEqual(rate_before, 1.0)
        self.assertLess(rate_after, 0.2)           # 采样率显著下降
        # 保留速率应贴近预算（50/s ± 30%）
        kept_per_sec = kept / 5.0
        self.assertGreater(kept_per_sec, 50 * 0.7)
        self.assertLess(kept_per_sec, 50 * 1.3)


class TestRareAnomalies(unittest.TestCase):
    """异常链路稀少：异常必须 100% 保留，即使正常链路被大幅降采。"""

    def test_rare_errors_fully_kept(self):
        clock = FakeClock()
        s = make_sampler(clock, budget=50.0, window=2.0)
        feed(s, clock, rps=400, seconds=3)         # 先把采样率压下去
        self.assertLess(s.sample_rate(), 0.3)
        offered, kept, err_offered, err_kept = feed(
            s, clock, rps=400, seconds=5, error_ratio=0.005, trace_prefix="rare")
        self.assertGreater(err_offered, 0)
        self.assertEqual(err_offered, err_kept)    # 异常保留率 = 100%
        normal_kept = kept - err_kept
        normal_offered = offered - err_offered
        self.assertLess(normal_kept / normal_offered, 0.3)


class TestBudgetExhausted(unittest.TestCase):
    """预算耗尽：硬上限打满后正常链路全丢，异常链路仍保留。"""

    def test_hard_cap_exhaustion(self):
        clock = FakeClock()
        s = make_sampler(clock, budget=10.0, window=1.0, hard_cap=10)
        kept_normal = 0
        # 同一秒内灌入 100 条正常链路：前 ~10 条保留，之后全丢。
        for i in range(100):
            if s.should_sample(f"flood-{i}"):
                kept_normal += 1
            clock.advance(0.001)
        self.assertLessEqual(kept_normal, 10)
        # 预算耗尽后异常链路仍然保留。
        err_kept = sum(
            s.should_sample(f"err-{i}", is_error=True) for i in range(5))
        self.assertEqual(err_kept, 5)


class TestTraceConsistency(unittest.TestCase):
    """链级一致性：同一 trace_id 的所有 span 决定一致。"""

    def test_same_trace_same_decision(self):
        clock = FakeClock()
        s = make_sampler(clock, budget=50.0, window=2.0)
        feed(s, clock, rps=300, seconds=3)         # 让采样率 < 1，制造丢与留
        self.assertLess(s.sample_rate(), 1.0)

        now = clock.t
        for trace_idx in range(500):
            trace_id = f"trace-{trace_idx}"
            decisions = {
                s.should_sample(trace_id, now=now)   # 同一时刻重复判定
                for _ in range(8)                     # 模拟一条链路的 8 个 span
            }
            self.assertEqual(
                len(decisions), 1,
                f"trace {trace_id} 的 span 决定不一致: {decisions}")

    def test_decision_deterministic_across_instances(self):
        """相同 trace_id 在不同采样器实例、相同采样率下决定一致。"""
        clock = FakeClock()
        s1 = make_sampler(clock, budget=50.0)
        s2 = make_sampler(clock, budget=50.0)
        for i in range(200):
            tid = f"x-{i}"
            self.assertEqual(s1.should_sample(tid), s2.should_sample(tid))

    def test_monotonic_threshold(self):
        """采样率升高时，原本保留的链路仍然保留（阈值单调）。"""
        from src.adaptive_sampler import _hash_trace, _HASH_SPACE
        kept_at_low = set()
        p_low, p_high = 0.1, 0.5
        for i in range(2000):
            tid = f"m-{i}"
            if _hash_trace(tid) < int(p_low * _HASH_SPACE):
                kept_at_low.add(tid)
        for tid in kept_at_low:
            self.assertLess(_hash_trace(tid), int(p_high * _HASH_SPACE))


if __name__ == "__main__":
    unittest.main()
