"""回拨场景行为说明与测试。

场景设定：墙上时间从 1000 出发，token ttl=10（1010 过期），
相对超时 100s（deadline = mono 100），重试退避每次 30s。

行为对照表：

+--------------------------+--------------------------+--------------------------+
| 场景                     | 旧代码（全用 time.time） | 新代码（wall/mono 分离） |
+==========================+==========================+==========================+
| 墙上回拨 500s            | 已过期 token "复活"；    | token 同样"复活"         |
| (1005 -> 505)            | 100s 超时被延长到 ~605s；| （绝对时间点语义本就如此）|
|                          | 重试计时被延长           | 但超时/重试走 mono，免疫  |
+--------------------------+--------------------------+--------------------------+
| 墙上前跳 500s            | 未过期 token 提前失效；  | token 提前失效（符合绝对  |
| (1005 -> 1505)           | 超时提前触发；重试被压缩 | exp 语义）；超时/重试免疫 |
+--------------------------+--------------------------+--------------------------+
| 单调时钟尝试回退         | —（无法表达）            | FakeClock 直接拒绝；      |
|                          |                          | SystemClock max() 钳制    |
+--------------------------+--------------------------+--------------------------+

token"复活"是墙上时间绝对过期点的固有特性（对端 exp 就是真实时间点），
工程上用 skew 容差与拒绝签发超长 ttl 缓解；超时/重试则绝不能受其影响。
"""

import unittest
from unittest import mock

from clocktime.clock import FakeClock
from clocktime.timing import AbsoluteExpiry, RetryPolicy, Timeout, run_with_retry
from legacy import legacy_timeouts as legacy


class TestWallClockRollback(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(start_wall=1000.0, start_mono=0.0)
        patches = [
            mock.patch.object(legacy.time, "time", side_effect=self.clock.wall_now),
            mock.patch.object(legacy.time, "sleep", side_effect=self.clock.advance),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_expired_token_revives_identically_in_both(self):
        """回拨让绝对过期点的 token 复活：两套实现观察一致（语义固有）。"""
        old_exp = legacy.make_token_expiry(10.0)
        new_exp = AbsoluteExpiry.ttl(self.clock, 10.0)
        self.clock.set_wall(1005.0)
        self.assertFalse(legacy.is_token_expired(old_exp))
        self.assertFalse(new_exp.is_expired(self.clock))
        self.clock.set_wall(1015.0)  # 已过期
        self.assertTrue(legacy.is_token_expired(old_exp))
        self.assertTrue(new_exp.is_expired(self.clock))
        self.clock.set_wall(505.0)   # 回拨 510s：两边一致地"复活"
        self.assertFalse(legacy.is_token_expired(old_exp))
        self.assertFalse(new_exp.is_expired(self.clock))
        self.assertGreater(legacy.token_seconds_remaining(old_exp), 500.0)
        self.assertGreater(new_exp.seconds_remaining(self.clock), 500.0)

    def test_legacy_timeout_is_stretched_by_rollback_new_is_immune(self):
        """旧代码的超时基于墙上时间，回拨会延长；新代码基于 mono，免疫。"""
        self.clock.set_wall(1000.0)
        old_deadline = legacy.timeout_deadline(100.0)
        new_timeout = Timeout(self.clock, 100.0)

        self.clock.set_wall(1005.0)
        self.assertFalse(legacy.is_timeout_expired(old_deadline))
        self.assertFalse(new_timeout.expired(self.clock))

        # 墙上时间回拨 500s
        self.clock.set_wall(505.0)
        # 旧代码：还要再等约 595s 墙上时间才超时 —— 超时被显著拉长
        self.assertFalse(legacy.is_timeout_expired(old_deadline))
        self.assertAlmostEqual(legacy.timeout_remaining(old_deadline), 595.0)
        # 新代码：mono 没动，剩余时间原样
        self.assertFalse(new_timeout.expired(self.clock))
        self.assertEqual(new_timeout.remaining(self.clock), 100.0)

        # mono 前进 100s（真实经过的时间），新代码准时超时；
        # 旧代码的墙上时间仍停在 505，未超时。
        self.clock.advance(100.0)
        self.assertTrue(new_timeout.expired(self.clock))
        self.assertFalse(legacy.is_timeout_expired(old_deadline))

    def test_legacy_timeout_jumps_forward_new_is_immune(self):
        """墙上时间前跳：旧代码超时提前触发，新代码不受影响。"""
        old_deadline = legacy.timeout_deadline(100.0)
        new_timeout = Timeout(self.clock, 100.0)
        self.clock.set_wall(1505.0)  # 前跳 505s
        self.assertTrue(legacy.is_timeout_expired(old_deadline))
        self.assertFalse(new_timeout.expired(self.clock))
        self.assertEqual(new_timeout.remaining(self.clock), 100.0)

    def test_retry_sleep_immune_to_rollback(self):
        """回拨不影响新代码的重试计时；sleep 由 mono 推进驱动，完全确定。"""
        policy = RetryPolicy(max_attempts=4, base_delay=30.0,
                             multiplier=1.0, max_delay=30.0)
        slept = []
        orig_sleep = self.clock.sleep
        self.clock.sleep = lambda s: (slept.append(s), orig_sleep(s))

        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            if attempts["n"] == 2:
                self.clock.set_wall(self.clock.wall_now() - 500.0)  # 回拨
            if attempts["n"] < 4:
                raise ConnectionError
            return "recovered"

        self.assertEqual(run_with_retry(flaky, policy, self.clock),
                         "recovered")
        self.assertEqual(slept, [30.0, 30.0, 30.0])
        self.assertEqual(self.clock.mono_now(), 90.0)   # 退避总时长确定
        self.assertEqual(self.clock.wall_now(), 590.0)  # wall 只受回拨影响

    def test_monotonic_attempt_to_go_back_is_rejected(self):
        with self.assertRaises(ValueError):
            self.clock.set_mono(-1.0)
        self.clock.advance(5.0)
        with self.assertRaises(ValueError):
            self.clock.set_mono(4.999)
        self.assertEqual(self.clock.mono_now(), 5.0)

    def test_wall_rollback_cannot_move_mono(self):
        self.clock.advance(42.0)
        wall_before = self.clock.wall_now()
        self.clock.set_wall(wall_before - 999.0)
        self.assertEqual(self.clock.mono_now(), 42.0)  # mono 纹丝不动


if __name__ == "__main__":
    unittest.main()
