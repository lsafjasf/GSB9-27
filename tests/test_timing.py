"""确定性回归测试：注入 FakeClock 后，所有逻辑无需真实等待。"""

import time
import unittest

from src.clocks import FakeClock
from src import timing


def failing(times, exc=RuntimeError("boom")):
    """前 times 次抛异常，之后成功返回 ok。"""
    state = {"calls": 0}

    def operation():
        state["calls"] += 1
        if state["calls"] <= times:
            raise exc
        return "ok"

    operation.state = state
    return operation


class ExpiryTest(unittest.TestCase):
    def test_boundary_is_inclusive(self):
        clock = FakeClock(wall=999.999)
        self.assertFalse(timing.is_expired(1000.0, clock))
        clock.set_wall(1000.0)
        self.assertTrue(timing.is_expired(1000.0, clock))
        clock.set_wall(1000.001)
        self.assertTrue(timing.is_expired(1000.0, clock))

    def test_wall_follows_calendar_semantics_even_backwards(self):
        # 墙上时间回拨时，"绝对时刻过期"会回到未过期：这是墙上语义的预期行为。
        clock = FakeClock(wall=2000.0)
        self.assertTrue(timing.is_expired(1500.0, clock))
        clock.set_wall(1000.0)  # NTP 回拨
        self.assertFalse(timing.is_expired(1500.0, clock))


class RemainingTest(unittest.TestCase):
    def test_counts_down_and_clamps(self):
        clock = FakeClock()
        self.assertEqual(timing.remaining(0.0, 10.0, clock), 10.0)
        clock.advance(4.0)
        self.assertEqual(timing.remaining(0.0, 10.0, clock), 6.0)
        clock.advance(7.0)
        self.assertEqual(timing.remaining(0.0, 10.0, clock), 0.0)

    def test_wall_rollback_does_not_affect_monotonic_remaining(self):
        # 回拨墙上时间不影响基于单调时间的超时判定。
        clock = FakeClock()
        clock.advance(3.0)
        before = timing.remaining(0.0, 10.0, clock)
        clock.set_wall(clock.wall() - 1_000_000.0)  # 大幅回拨
        after = timing.remaining(0.0, 10.0, clock)
        self.assertEqual(before, after)


class RetryTest(unittest.TestCase):
    def test_success_on_third_attempt_uses_exact_backoff(self):
        clock = FakeClock()
        op = failing(2)
        real_start = time.perf_counter()
        result = timing.retry(op, clock, max_attempts=5,
                              base_delay=0.1, max_delay=1.0, timeout=10.0)
        real_elapsed = time.perf_counter() - real_start

        self.assertEqual(result, "ok")
        self.assertEqual(op.state["calls"], 3)
        # 退避 0.1 + 0.2，假时钟恰好前进 0.3 秒
        self.assertAlmostEqual(clock.monotonic(), 0.3)
        self.assertAlmostEqual(clock.slept_total, 0.3)
        # 没有任何真实等待
        self.assertLess(real_elapsed, 1.0)

    def test_backoff_caps_at_max_delay(self):
        clock = FakeClock()
        op = failing(10)
        with self.assertRaises(RuntimeError):
            timing.retry(op, clock, max_attempts=6,
                         base_delay=1.0, max_delay=4.0, timeout=100.0)
        # 1, 2, 4, 4, 4
        self.assertAlmostEqual(clock.slept_total, 15.0)

    def test_max_attempts_exhausted_reraises_original(self):
        clock = FakeClock()
        boom = ValueError("specific")
        op = failing(10, boom)
        with self.assertRaises(ValueError) as ctx:
            timing.retry(op, clock, max_attempts=3, base_delay=0.01,
                         max_delay=0.01, timeout=100.0)
        self.assertIs(ctx.exception, boom)
        self.assertEqual(op.state["calls"], 3)
        self.assertAlmostEqual(clock.slept_total, 0.02)

    def test_deadline_gives_up_before_exceeding_timeout(self):
        clock = FakeClock()
        op = failing(10)
        with self.assertRaises(timing.DeadlineExpired):
            # 第一次失败后已过 4.8s，再退避 0.5s 会超过 5s 总超时
            timing.retry(_jitter_failing(op, clock, 4.8), clock,
                         max_attempts=5, base_delay=0.5, max_delay=5.0,
                         timeout=5.0)
        # 截止判定必须在 sleep 之前发生：不允许睡这 0.5s
        self.assertEqual(clock.slept_total, 4.8)  # 只有 jitter，没有 0.5s 退避
        self.assertEqual(op.state["calls"], 1)

    def test_deterministic_repeatability(self):
        def run():
            clock = FakeClock()
            op = failing(2)
            timing.retry(op, clock, max_attempts=5, base_delay=0.1,
                         max_delay=1.0, timeout=10.0)
            return clock.slept_total

        self.assertEqual(run(), run())


def _jitter_failing(op, clock, jitter):
    def wrapped():
        clock.advance(jitter)
        return op()
    return wrapped


if __name__ == "__main__":
    unittest.main()
