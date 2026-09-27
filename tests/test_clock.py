"""时钟抽象自身的测试：协议、假时钟确定性、多线程单调、跨线程唤醒。"""

import threading
import time
import unittest

from clocktime.clock import FakeClock, SystemClock, SYSTEM_CLOCK


class ClockContractMixin:
    """SystemClock / FakeClock 共同满足的接口契约。"""

    def make_clock(self):
        raise NotImplementedError

    def test_has_two_time_semantics(self):
        clock = self.make_clock()
        wall = clock.wall_now()
        mono = clock.mono_now()
        self.assertIsInstance(wall, float)
        self.assertIsInstance(mono, float)

    def test_sleep_rejects_negative(self):
        clock = self.make_clock()
        with self.assertRaises(ValueError):
            clock.sleep(-0.001)

    def test_zero_sleep_is_noop(self):
        clock = self.make_clock()
        before = clock.mono_now()
        clock.sleep(0)
        self.assertGreaterEqual(clock.mono_now(), before)


class TestSystemClock(ClockContractMixin, unittest.TestCase):
    def make_clock(self):
        return SystemClock()

    def test_wall_is_epoch_seconds(self):
        clock = self.make_clock()
        self.assertAlmostEqual(clock.wall_now(), time.time(), delta=5.0)

    def test_mono_tracks_system_monotonic(self):
        clock = self.make_clock()
        a = clock.mono_now()
        b = clock.mono_now()
        self.assertGreaterEqual(b, a)

    def test_singleton_exists(self):
        self.assertIsInstance(SYSTEM_CLOCK, SystemClock)


class TestFakeClock(ClockContractMixin, unittest.TestCase):
    def make_clock(self):
        return FakeClock(start_wall=1000.0, start_mono=10.0)

    def test_does_not_move_by_itself(self):
        clock = FakeClock(start_wall=1000.0, start_mono=10.0)
        time.sleep(0.02)  # 真实时间流过，但假时钟不应变化
        self.assertEqual(clock.wall_now(), 1000.0)
        self.assertEqual(clock.mono_now(), 10.0)

    def test_advance_moves_both_clocks_together(self):
        clock = FakeClock(start_wall=1000.0, start_mono=10.0)
        clock.advance(2.5)
        self.assertEqual(clock.mono_now(), 12.5)
        self.assertEqual(clock.wall_now(), 1002.5)

    def test_sleep_is_instant_advance_no_real_waiting(self):
        clock = FakeClock()
        start_real = time.monotonic()
        clock.sleep(3600.0)  # 逻辑上等 1 小时
        self.assertLess(time.monotonic() - start_real, 0.5)
        self.assertEqual(clock.mono_now(), 3600.0)

    def test_cannot_advance_backwards(self):
        clock = FakeClock()
        with self.assertRaises(ValueError):
            clock.advance(-1.0)

    def test_set_wall_may_go_backwards(self):
        clock = FakeClock(start_wall=1000.0)
        clock.set_wall(500.0)
        self.assertEqual(clock.wall_now(), 500.0)

    def test_set_mono_cannot_go_backwards(self):
        clock = FakeClock(start_mono=10.0)
        with self.assertRaises(ValueError):
            clock.set_mono(9.9)
        clock.set_mono(10.0)
        clock.set_mono(11.0)
        self.assertEqual(clock.mono_now(), 11.0)

    def test_readers_never_see_monotonic_regression_under_contention(self):
        """多线程高频读取：每个线程看到的单调时间必须非递减。"""
        clock = FakeClock()
        stop = threading.Event()
        regressions = []

        def reader():
            last = clock.mono_now()
            while not stop.is_set():
                current = clock.mono_now()
                if current < last:
                    regressions.append((last, current))
                    return
                last = current

        threads = [threading.Thread(target=reader) for _ in range(8)]
        for t in threads:
            t.start()
        for _ in range(1000):
            clock.advance(0.001)
        stop.set()
        for t in threads:
            t.join(timeout=2.0)
        self.assertEqual(regressions, [])

    def test_sleeping_thread_is_woken_by_external_advance(self):
        """工作线程在假时钟上 sleep，主线程推进时钟即可确定性唤醒。"""
        clock = FakeClock()
        woke_at = []
        done = threading.Event()

        def worker():
            clock.wait_until(5.0)
            woke_at.append(clock.mono_now())
            done.set()

        t = threading.Thread(target=worker)
        t.start()
        self.assertFalse(done.wait(timeout=0.1))
        clock.advance(5.0)
        self.assertTrue(done.wait(timeout=2.0))
        self.assertEqual(woke_at, [5.0])
        t.join(timeout=2.0)

    def test_system_clock_readers_never_regress_with_worker(self):
        """SystemClock 在多线程读取下同样不允许出现倒退。"""
        clock = SystemClock()
        stop = threading.Event()
        regressions = []

        def reader():
            last = clock.mono_now()
            while not stop.is_set():
                current = clock.mono_now()
                if current < last:
                    regressions.append((last, current))
                    return
                last = current

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            pass
        stop.set()
        for t in threads:
            t.join(timeout=2.0)
        self.assertEqual(regressions, [])


if __name__ == "__main__":
    unittest.main()
