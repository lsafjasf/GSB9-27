"""差分测试：重构前后在同一时刻序列上的每个判定必须一致。

做法：生成随机但非递减的时刻序列 t0, t1, ...（单调时钟与墙上时钟同步
前进，模拟"无校时"的正常世界），用一个 FakeClock 同时驱动：

- 新代码直接读 FakeClock；
- 旧代码通过 mock 让其 time.time() 返回 FakeClock.wall_now()、
  time.sleep() 变为 FakeClock.advance()（旧代码没有单调时钟概念）。

于是两套实现观察到完全相同的时刻序列，逐分支比较布尔判定与数值。
回拨导致的刻意差异由 test_rollback.py 单独覆盖。
"""

import random
import unittest
from unittest import mock

from clocktime.clock import FakeClock
from clocktime.timing import AbsoluteExpiry, RetryPolicy, Timeout, run_with_retry
from legacy import legacy_timeouts as legacy

# ---- 每个用例的初始条件在此显式声明，全文件唯一 ----
SEED = 20260927        # 随机种子：整个测试进程只在这一处确定
START_WALL = 5_000.0   # 假时钟墙上时间起点
START_MONO = 5_000.0   # 假时钟单调时间起点


class ClockDrivenCase(unittest.TestCase):
    def setUp(self):
        # 每个用例只初始化一次：种子与时钟起点都来自上面的常量。
        self.rng = random.Random(SEED)
        self.clock = self._new_clock()
        # 旧代码读到的 time.time() 与假时钟完全一致。
        # lambda 按名字引用 self.clock，_fresh_round() 换钟后 mock 自动
        # 跟随新时钟，无需也不允许在用例内重新 patch。
        patches = [
            mock.patch.object(legacy.time, "time", side_effect=lambda: self.clock.wall_now()),
            mock.patch.object(legacy.time, "sleep", side_effect=lambda s: self.clock.advance(s)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _new_clock(self):
        return FakeClock(start_wall=START_WALL, start_mono=START_MONO)

    def _fresh_round(self):
        """开启新一轮时刻序列：换一只全新假时钟（初始条件与 setUp 相同）。

        只换时钟，绝不动随机种子——rng 流在整个用例内连续推进，
        保证每一轮真的跑在不同的随机序列上。
        """
        self.clock = self._new_clock()

    def move_to_next(self):
        self.clock.advance(self.rng.uniform(0.0, 5.0))

    # ---- 绝对过期（墙上时间） ----

    def test_expiry_decisions_match_across_many_sequences(self):
        for round_no in range(300):
            self._fresh_round()
            with self.subTest(round=round_no):
                ttl = self.rng.choice([0.0, 0.5, 2.0, 10.0, 100.0])
                skew = self.rng.choice([0.0, 0.0, 0.25, 1.0])
                old_exp = legacy.make_token_expiry(ttl)
                new_exp = AbsoluteExpiry.ttl(self.clock, ttl)
                self.assertEqual(old_exp, new_exp.expires_at)
                for _ in range(self.rng.randrange(0, 12)):
                    self.move_to_next()
                    self.assertEqual(
                        legacy.is_token_expired(old_exp, skew),
                        new_exp.is_expired(self.clock, skew),
                    )
                    self.assertAlmostEqual(
                        legacy.token_seconds_remaining(old_exp, skew),
                        new_exp.seconds_remaining(self.clock, skew),
                        places=9,
                    )

    # ---- 相对超时（旧代码误用墙上时间；正常无校时时结果应一致） ----

    def test_timeout_decisions_match(self):
        for round_no in range(300):
            self._fresh_round()
            with self.subTest(round=round_no):
                seconds = self.rng.choice([0.0, 0.1, 3.0, 7.5, 50.0])
                old_deadline = legacy.timeout_deadline(seconds)
                new_timeout = Timeout(self.clock, seconds)
                self.assertEqual(old_deadline, new_timeout.deadline)
                for _ in range(self.rng.randrange(0, 12)):
                    self.move_to_next()
                    self.assertEqual(
                        legacy.is_timeout_expired(old_deadline),
                        new_timeout.expired(self.clock),
                    )
                    self.assertAlmostEqual(
                        legacy.timeout_remaining(old_deadline),
                        new_timeout.remaining(self.clock),
                        places=9,
                    )

    # ---- 重试判定与退避表 ----

    def test_retry_policy_matches(self):
        for _ in range(200):
            params = dict(
                max_attempts=self.rng.randrange(1, 8),
                base_delay=self.rng.choice([0.0, 0.01, 0.1, 0.5]),
                multiplier=self.rng.choice([1.0, 2.0, 3.0]),
                max_delay=self.rng.choice([0.05, 0.5, 5.0, 100.0]),
            )
            policy = RetryPolicy(**params)
            for attempt in range(1, params["max_attempts"] + 2):
                err = ConnectionError()
                self.assertEqual(
                    legacy.should_retry(attempt, params["max_attempts"]),
                    policy.should_retry(attempt, err),
                )
                self.assertAlmostEqual(
                    legacy.retry_delay(
                        attempt, params["base_delay"],
                        params["multiplier"], params["max_delay"]),
                    policy.delay_for(attempt),
                    places=9,
                )

    # ---- 端到端：同样的成功/失败脚本 ----

    def _make_script(self, fail_before, exc_factory):
        calls = {"n": 0}

        def func():
            calls["n"] += 1
            if calls["n"] <= fail_before:
                raise exc_factory()
            return ("ok", calls["n"])

        return func, calls

    def test_run_with_retry_success_paths_match(self):
        for fail_before in range(0, 4):
            self._fresh_round()
            with self.subTest(fail_before=fail_before):
                params = dict(max_attempts=5, base_delay=0.1,
                              multiplier=2.0, max_delay=0.3)
                policy = RetryPolicy(**params)
                old_func, old_calls = self._make_script(fail_before, ConnectionError)
                new_func, new_calls = self._make_script(fail_before, ConnectionError)

                old_slept = []
                new_clock = self._new_clock()
                new_slept = []

                with mock.patch.object(legacy.time, "sleep",
                                       side_effect=lambda s: (old_slept.append(s),
                                                              self.clock.advance(s))):
                    old_result = legacy.run_with_retry(old_func, **params)
                orig_new_sleep = new_clock.sleep
                new_clock.sleep = lambda s: (new_slept.append(s), orig_new_sleep(s))
                new_result = run_with_retry(new_func, policy, new_clock)

                self.assertEqual(old_result, new_result)
                self.assertEqual(old_calls["n"], new_calls["n"])
                self.assertEqual(old_slept, new_slept)
                self.assertEqual(self.clock.mono_now(), new_clock.mono_now())

    def test_run_with_retry_exhaustion_matches(self):
        params = dict(max_attempts=3, base_delay=0.2,
                      multiplier=2.0, max_delay=1.0)
        new_clock = self._new_clock()

        old_err = new_err = None
        try:
            legacy.run_with_retry(lambda: (_ for _ in ()).throw(ValueError("x")),
                                  **params)
        except RuntimeError as exc:
            old_err = exc
        try:
            run_with_retry(
                lambda: (_ for _ in ()).throw(ValueError("x")),
                RetryPolicy(**params), new_clock)
        except Exception as exc:
            new_err = exc

        self.assertIsNotNone(old_err)
        self.assertIsNotNone(new_err)
        self.assertIn("3 attempt", str(old_err))
        self.assertEqual(new_err.attempts, 3)
        self.assertEqual(repr(new_err.last_error),
                         str(old_err).split(": ", 1)[1])
        self.assertAlmostEqual(self.clock.mono_now(), new_clock.mono_now(),
                               places=9)
        self.assertGreater(self.clock.mono_now(), START_MONO)  # 退避确实发生


if __name__ == "__main__":
    unittest.main()
