"""差分测试：同一时刻序列下，重构前后的分支判定必须完全一致。

做法：给 legacy_timing 打桩，让它读取的 time.time/time.sleep 与
重构版注入的 FakeClock 走同一套时间序列，然后逐场景比较结果。

时间序列由固定随机种子生成，包含正常流逝、向前跳变，以及墙上时间
回拨（仅墙上语义的 is_expired 用例）。
"""

import random
import unittest

from src.clocks import FakeClock
from src import legacy_timing, timing


class LegacyShim:
    """让 legacy_timing 模块读到的 time.time/time.sleep 指向假时钟。"""

    def __init__(self, clock, source):
        self._clock = clock
        self._source = source

    def time(self):
        return getattr(self._clock, self._source)()

    def sleep(self, seconds):
        self._clock.sleep(seconds)

    @staticmethod
    def patch(testcase, clock, source):
        shim = LegacyShim(clock, source)
        testcase.addCleanup(setattr, legacy_timing, "time", legacy_timing.time)
        legacy_timing.time = shim
        return shim


class ExpiryDifferentialTest(unittest.TestCase):
    """is_expired：墙上时间，允许回拨序列，两版判定必须逐点一致。"""

    def test_random_wall_sequences(self):
        rng = random.Random(264)
        for _ in range(200):
            clock = FakeClock(wall=rng.uniform(900.0, 1100.0))
            LegacyShim.patch(self, clock, "wall")
            expires_at = rng.uniform(950.0, 1050.0)
            wall = clock.wall()
            for _ in range(30):
                if rng.random() < 0.3:
                    # 回拨或大幅向前跳变
                    wall += rng.uniform(-120.0, 120.0)
                else:
                    wall += rng.uniform(0.0, 5.0)
                clock.set_wall(wall)
                self.assertIs(
                    timing.is_expired(expires_at, clock),
                    legacy_timing.is_expired(expires_at),
                )


class RemainingDifferentialTest(unittest.TestCase):
    """remaining：单调不减序列，两版剩余值必须相等（含截断到 0）。"""

    def test_random_monotonic_sequences(self):
        rng = random.Random(265)
        for _ in range(200):
            clock = FakeClock()
            LegacyShim.patch(self, clock, "monotonic")
            duration = rng.uniform(0.0, 10.0)
            elapsed = 0.0
            for _ in range(20):
                elapsed += rng.uniform(0.0, 1.5)
                clock.advance(rng.uniform(0.0, 1.5))
                self.assertAlmostEqual(
                    timing.remaining(0.0, duration, clock),
                    legacy_timing.remaining(0.0, duration),
                    places=12,
                )


class RetryDifferentialTest(unittest.TestCase):
    """retry：随机参数 + 随机失败次数/耗时抖动，比较全部分支结果。"""

    def run_one(self, params, fail_times, jitters):
        # 重构版
        new_clock = FakeClock()

        def make_op(clock):
            calls = {"n": 0}

            def operation():
                clock.advance(jitters[calls["n"]])
                if calls["n"] < fail_times:
                    calls["n"] += 1
                    raise RuntimeError("boom %d" % calls["n"])
                calls["n"] += 1
                return "ok"
            return operation, calls

        def outcome(get_result):
            try:
                return ("ok", get_result(), None)
            except timing.DeadlineExpired:
                return ("deadline", None, None)
            except legacy_timing.DeadlineExpired:
                return ("deadline", None, None)
            except RuntimeError:
                return ("exhausted", None, RuntimeError)

        # 新版
        new_op, new_calls = make_op(new_clock)
        new_outcome = outcome(
            lambda: timing.retry(new_op, new_clock, **params))

        # 旧版（独立假时钟，同样的流逝/睡眠计划，保证序列相同）
        old_clock = FakeClock()
        old_op, old_calls = make_op(old_clock)
        LegacyShim.patch(self, old_clock, "monotonic")
        old_outcome = outcome(
            lambda: legacy_timing.retry(old_op, **params))

        self.assertEqual(new_outcome, old_outcome)
        self.assertEqual(new_calls["n"], old_calls["n"])
        self.assertAlmostEqual(new_clock.slept_total,
                               old_clock.slept_total, places=12)

    def test_random_scenarios(self):
        rng = random.Random(266)
        for _ in range(500):
            max_attempts = rng.randint(1, 6)
            params = dict(
                max_attempts=max_attempts,
                base_delay=rng.choice([0.0, 0.01, 0.1, 0.5]),
                max_delay=rng.choice([0.1, 1.0, 4.0]),
                timeout=rng.choice([0.05, 0.5, 2.0, 10.0, 100.0]),
            )
            fail_times = rng.randint(0, max_attempts + 2)
            jitters = [rng.uniform(0.0, 1.0)
                       for _ in range(max_attempts + 2)]
            self.run_one(params, fail_times, jitters)


if __name__ == "__main__":
    unittest.main()
