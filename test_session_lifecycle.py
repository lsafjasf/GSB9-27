"""会话生命周期自测：持续活跃 / 长时间挂起 / 到达绝对期限 / 并发续期 / 边界。"""

import threading
import unittest

from session_lifecycle import (
    ExpireReason,
    Session,
    SessionConfig,
    SessionExpiredError,
    SessionState,
)


class ManualClock:
    """可注入的手动时钟。"""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


def make_session(clock, idle=60.0, absolute=300.0):
    return Session(SessionConfig(idle_timeout=idle, absolute_timeout=absolute),
                   clock=clock)


class TestIdleTimeout(unittest.TestCase):
    def test_long_suspension_expires_by_idle(self):
        """长时间挂起：无任何活动，到达空闲超时即过期。"""
        clock = ManualClock()
        s = make_session(clock, idle=60.0, absolute=300.0)

        clock.advance(59.9)
        snap = s.inspect()
        self.assertIs(snap.state, SessionState.ACTIVE)
        self.assertAlmostEqual(snap.idle_remaining, 0.1)

        clock.advance(0.1)  # 恰好到达空闲到期时刻
        snap = s.inspect()
        self.assertIs(snap.state, SessionState.EXPIRED)
        self.assertIs(snap.reason, ExpireReason.IDLE_TIMEOUT)
        self.assertEqual(snap.idle_expires_at, 1060.0)
        self.assertEqual(snap.absolute_expires_at, 1300.0)
        self.assertEqual(snap.effective_expires_at, 1060.0)

    def test_idle_boundary_exact_is_expired(self):
        """边界：now == idle_expires_at 判定为过期。"""
        clock = ManualClock()
        s = make_session(clock, idle=60.0)
        clock.advance(60.0)
        self.assertFalse(s.is_alive())
        clock.t = 1059.999
        self.assertTrue(s.is_alive())


class TestContinuousActivity(unittest.TestCase):
    def test_continuous_activity_survives_beyond_one_idle_window(self):
        """持续活跃：不断续期，会话可活过多个空闲窗口，但最终死于绝对期限。"""
        clock = ManualClock()
        s = make_session(clock, idle=60.0, absolute=300.0)

        # 每 50s 活动一次，共 5 次 -> 走到 t=1250，远超单个空闲窗口
        for _ in range(5):
            clock.advance(50.0)
            result = s.renew()
            self.assertTrue(result.absolute_expiry_unchanged)
            self.assertIs(result.after.state, SessionState.ACTIVE)

        snap = s.inspect()
        self.assertEqual(snap.last_activity_at, 1250.0)
        self.assertEqual(snap.idle_expires_at, 1310.0)
        # 实际到期被绝对期限截断
        self.assertEqual(snap.effective_expires_at, 1300.0)
        self.assertIs(snap.state, SessionState.ACTIVE)

        # 即使再活跃，到达绝对期限仍然过期
        clock.t = 1300.0
        snap = s.inspect()
        self.assertIs(snap.state, SessionState.EXPIRED)
        self.assertIs(snap.reason, ExpireReason.ABSOLUTE_TIMEOUT)

    def test_renewal_reports_before_and_after_expiry(self):
        """续期给出前后到期时刻，且只延长空闲时间。"""
        clock = ManualClock()
        s = make_session(clock, idle=60.0, absolute=300.0)
        clock.advance(40.0)
        result = s.renew()

        self.assertEqual(result.before.idle_expires_at, 1060.0)
        self.assertEqual(result.after.idle_expires_at, 1100.0)
        self.assertEqual(result.idle_expiry_extended_by, 40.0)
        self.assertEqual(result.before.absolute_expires_at, 1300.0)
        self.assertEqual(result.after.absolute_expires_at, 1300.0)

    def test_renewal_never_extends_absolute_deadline(self):
        """在绝对期限附近续期：effective 到期时刻仍被绝对期限钳制。"""
        clock = ManualClock()
        s = make_session(clock, idle=60.0, absolute=300.0)
        # 保持活跃直到 1290
        while clock.t + 50.0 <= 1290.0:
            clock.advance(50.0)
            s.renew()
        clock.t = 1290.0  # 距绝对期限 10s
        result = s.renew()
        self.assertEqual(result.after.idle_expires_at, 1350.0)      # 空闲窗口到 1350
        self.assertEqual(result.after.effective_expires_at, 1300.0)  # 但实际仍是 1300
        clock.t = 1300.0
        self.assertFalse(s.is_alive())


class TestAbsoluteTimeout(unittest.TestCase):
    def test_absolute_boundary_exact_is_expired(self):
        """边界：now == absolute_expires_at 判定为过期，原因是绝对超时。"""
        clock = ManualClock()
        s = make_session(clock, idle=60.0, absolute=300.0)
        while clock.t + 50.0 <= 1290.0:
            clock.advance(50.0)
            s.renew()
        clock.t = 1299.999
        self.assertTrue(s.is_alive())
        clock.t = 1300.0
        snap = s.inspect()
        self.assertIs(snap.reason, ExpireReason.ABSOLUTE_TIMEOUT)
        self.assertEqual(snap.absolute_remaining, 0.0)

    def test_expired_session_rejects_renewal(self):
        """过期会话拒绝续期，并携带过期原因。"""
        clock = ManualClock()
        s = make_session(clock, idle=60.0)
        clock.advance(60.0)
        with self.assertRaises(SessionExpiredError) as ctx:
            s.renew()
        self.assertIs(ctx.exception.reason, ExpireReason.IDLE_TIMEOUT)
        self.assertEqual(ctx.exception.snapshot.idle_expires_at, 1060.0)

    def test_absolute_expired_rejects_renewal(self):
        clock = ManualClock()
        s = make_session(clock, idle=60.0, absolute=300.0)
        clock.t = 1300.0
        with self.assertRaises(SessionExpiredError) as ctx:
            s.renew()
        self.assertIs(ctx.exception.reason, ExpireReason.ABSOLUTE_TIMEOUT)


class TestConcurrentRenewal(unittest.TestCase):
    def test_concurrent_renewals_do_not_overwrite_each_other(self):
        """并发续期：N 个线程同时续期，最终到期时刻确定且等于 max(续期时刻)+idle。"""
        clock = ManualClock()
        s = make_session(clock, idle=60.0, absolute=10_000.0)

        n_threads = 32
        renew_times = [1000.0 + i * 0.5 for i in range(n_threads)]  # 1000.0 .. 1015.5
        barrier = threading.Barrier(n_threads)
        results, errors = [], []

        def worker(t):
            try:
                barrier.wait(timeout=5)  # 尽量同时放行
                results.append(s.renew(now=t))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in renew_times]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), n_threads)
        # 最终状态确定：last_activity = 最大续期时刻，与调度顺序无关
        snap = s.inspect(now=1015.5)
        self.assertEqual(snap.last_activity_at, max(renew_times))
        self.assertEqual(snap.idle_expires_at, max(renew_times) + 60.0)
        self.assertEqual(snap.effective_expires_at, 1075.5)
        # 每次续期都返回了自洽的前后快照，且绝对期限从未变化
        for r in results:
            self.assertTrue(r.absolute_expiry_unchanged)
            self.assertGreaterEqual(r.after.idle_expires_at, r.before.idle_expires_at)

    def test_concurrent_renewal_with_expired_loser(self):
        """并发场景：一个线程续期成功，另一个在会话已过期后到达则被拒绝。"""
        clock = ManualClock()
        s = make_session(clock, idle=60.0, absolute=300.0)
        clock.t = 1059.0
        ok = s.renew()  # 续到 1119
        self.assertEqual(ok.after.idle_expires_at, 1119.0)
        clock.t = 1119.0  # 恰好到期
        with self.assertRaises(SessionExpiredError):
            s.renew()
        # 最终到期时刻仍是 1119，未被失败续期污染
        self.assertEqual(s.inspect().idle_expires_at, 1119.0)

    def test_out_of_order_renewal_does_not_regress(self):
        """乱序续期（时钟回拨）：较晚的 last_activity 不被较早的续期覆盖。"""
        clock = ManualClock()
        s = make_session(clock, idle=60.0)
        s.renew(now=1050.0)
        result = s.renew(now=1030.0)  # 旧时间戳的续期迟到
        self.assertEqual(result.after.last_activity_at, 1050.0)
        self.assertEqual(result.after.idle_expires_at, 1110.0)


class TestJudgementData(unittest.TestCase):
    def test_snapshot_contains_full_judgement_data(self):
        """判定数据完整性：两种超时的基准、到期时刻、剩余时间、状态与原因。"""
        clock = ManualClock()
        s = make_session(clock, idle=60.0, absolute=300.0)
        clock.advance(45.0)
        snap = s.inspect()
        self.assertEqual(snap.created_at, 1000.0)
        self.assertEqual(snap.last_activity_at, 1000.0)
        self.assertEqual(snap.idle_expires_at, 1060.0)
        self.assertEqual(snap.absolute_expires_at, 1300.0)
        self.assertEqual(snap.effective_expires_at, 1060.0)
        self.assertEqual(snap.idle_remaining, 15.0)
        self.assertEqual(snap.absolute_remaining, 255.0)
        self.assertIs(snap.state, SessionState.ACTIVE)
        self.assertIs(snap.reason, ExpireReason.NONE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
