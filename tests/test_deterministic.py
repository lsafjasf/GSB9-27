"""注入 FakeClock 后，超时/过期/重试全部确定性可测，无任何真实等待。"""

import threading
import unittest

from clocktime.clock import FakeClock
from clocktime.timing import (
    AbsoluteExpiry,
    RetryError,
    RetryPolicy,
    Timeout,
    run_with_retry,
)


class TestAbsoluteExpiry(unittest.TestCase):
    def test_ttl_constructed_from_wall_time(self):
        clock = FakeClock(start_wall=1000.0)
        expiry = AbsoluteExpiry.ttl(clock, 60.0)
        self.assertEqual(expiry.expires_at, 1060.0)
        self.assertFalse(expiry.is_expired(clock))

    def test_expires_exactly_at_boundary(self):
        clock = FakeClock(start_wall=1000.0)
        expiry = AbsoluteExpiry(1010.0)
        self.assertFalse(expiry.is_expired(clock))
        clock.set_wall(1009.999)
        self.assertFalse(expiry.is_expired(clock))
        clock.set_wall(1010.0)
        self.assertTrue(expiry.is_expired(clock))  # 边界：now >= exp

    def test_skew_tolerance(self):
        clock = FakeClock(start_wall=1000.0)
        expiry = AbsoluteExpiry(1010.0)
        self.assertFalse(expiry.is_expired(clock, skew_seconds=5.0))
        clock.set_wall(1005.0)
        self.assertTrue(expiry.is_expired(clock, skew_seconds=5.0))

    def test_seconds_remaining(self):
        clock = FakeClock(start_wall=1000.0)
        expiry = AbsoluteExpiry(1007.5)
        self.assertEqual(expiry.seconds_remaining(clock), 7.5)


class TestTimeout(unittest.TestCase):
    def test_based_on_monotonic_clock(self):
        clock = FakeClock(start_wall=1000.0, start_mono=0.0)
        timeout = Timeout(clock, 3.0)
        self.assertEqual(timeout.deadline, 3.0)
        self.assertFalse(timeout.expired(clock))
        clock.advance(2.0)
        self.assertFalse(timeout.expired(clock))
        self.assertEqual(timeout.remaining(clock), 1.0)
        clock.advance(1.0)
        self.assertTrue(timeout.expired(clock))
        self.assertEqual(timeout.remaining(clock), 0.0)

    def test_wall_jump_does_not_affect_timeout(self):
        clock = FakeClock(start_wall=1000.0)
        timeout = Timeout(clock, 10.0)
        clock.set_wall(9_999_999.0)  # 墙上时间狂跳
        self.assertFalse(timeout.expired(clock))
        self.assertEqual(timeout.remaining(clock), 10.0)

    def test_zero_timeout_is_already_expired(self):
        clock = FakeClock()
        self.assertTrue(Timeout(clock, 0.0).expired(clock))

    def test_negative_timeout_rejected(self):
        with self.assertRaises(ValueError):
            Timeout(FakeClock(), -1.0)


class TestRetryPolicy(unittest.TestCase):
    def test_backoff_schedule_with_cap(self):
        policy = RetryPolicy(max_attempts=6, base_delay=0.1,
                             multiplier=2.0, max_delay=0.5)
        self.assertEqual(
            [policy.delay_for(n) for n in range(1, 6)],
            [0.1, 0.2, 0.4, 0.5, 0.5],
        )

    def test_should_retry_respects_max_attempts(self):
        policy = RetryPolicy(max_attempts=3)
        err = ValueError("boom")
        self.assertTrue(policy.should_retry(1, err))
        self.assertTrue(policy.should_retry(2, err))
        self.assertFalse(policy.should_retry(3, err))

    def test_should_retry_respects_predicate(self):
        policy = RetryPolicy(
            retry_on=lambda exc: isinstance(exc, ConnectionError))
        self.assertTrue(policy.should_retry(1, ConnectionError()))
        self.assertFalse(policy.should_retry(1, ValueError()))

    def test_invalid_construction(self):
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=0)
        with self.assertRaises(ValueError):
            RetryPolicy(multiplier=0.5)


class TestRunWithRetryDeterministic(unittest.TestCase):
    def test_succeeds_on_third_attempt_records_sleeps(self):
        clock = FakeClock(start_wall=0.0)
        slept = []
        original_sleep = clock.sleep
        clock.sleep = lambda s: (slept.append(s), original_sleep(s))
        policy = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=10.0)

        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("reset")
            return "ok"

        result = run_with_retry(flaky, policy, clock)
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(slept, [1.0, 2.0])          # 退避完全确定
        self.assertEqual(clock.mono_now(), 3.0)      # 无真实等待
        self.assertEqual(clock.wall_now(), 3.0)

    def test_exhausts_attempts_and_raises_retry_error(self):
        clock = FakeClock()
        policy = RetryPolicy(max_attempts=3, base_delay=0.5, max_delay=0.5)

        def always_fails():
            raise ValueError("nope")

        with self.assertRaises(RetryError) as ctx:
            run_with_retry(always_fails, policy, clock)
        self.assertEqual(ctx.exception.attempts, 3)
        self.assertIsInstance(ctx.exception.last_error, ValueError)
        self.assertEqual(clock.mono_now(), 1.0)  # 两次 0.5s 退避

    def test_non_retryable_exception_fails_fast(self):
        clock = FakeClock()
        policy = RetryPolicy(
            retry_on=lambda exc: isinstance(exc, ConnectionError))

        def raises_type_error():
            raise TypeError("fatal")

        with self.assertRaises(RetryError) as ctx:
            run_with_retry(raises_type_error, policy, clock)
        self.assertEqual(ctx.exception.attempts, 1)
        self.assertEqual(clock.mono_now(), 0.0)  # 一次都没睡

    def test_first_try_success_does_not_advance_clock(self):
        clock = FakeClock(start_wall=42.0, start_mono=7.0)
        policy = RetryPolicy()
        self.assertEqual(run_with_retry(lambda: 123, policy, clock), 123)
        self.assertEqual(clock.wall_now(), 42.0)
        self.assertEqual(clock.mono_now(), 7.0)

    def test_deterministic_across_runs(self):
        """同样的脚本跑两遍，结果（含所有中间时刻）必须逐位相同。"""

        def scenario():
            clock = FakeClock()
            policy = RetryPolicy(base_delay=0.25)
            seq = []
            n = {"i": 0}

            def flaky():
                n["i"] += 1
                seq.append(clock.mono_now())
                if n["i"] <= 2:
                    raise ConnectionError
                return "done"

            result = run_with_retry(flaky, policy, clock)
            return result, seq, clock.mono_now()

        self.assertEqual(scenario(), scenario())


if __name__ == "__main__":
    unittest.main()
