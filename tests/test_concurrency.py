"""多线程读取时钟与系统时间回拨场景测试。"""

import threading
import unittest

from src.clocks import FakeClock, SystemClock
from src import timing


class FakeClockConcurrencyTest(unittest.TestCase):
    def test_monotonic_never_regresses_under_concurrent_reads(self):
        clock = FakeClock()
        stop = threading.Event()
        reader_errors = []

        def reader():
            last = clock.monotonic()
            while not stop.is_set():
                cur = clock.monotonic()
                if cur < last:
                    reader_errors.append((last, cur))
                    return
                last = cur

        threads = [threading.Thread(target=reader) for _ in range(8)]
        for t in threads:
            t.start()
        # 另一线程不断正常推进、以及把墙上时间大幅回拨
        for i in range(10_000):
            if i % 3 == 0:
                clock.set_wall(clock.wall() - 7.0)  # 回拨
            else:
                clock.advance(0.001)
        stop.set()
        for t in threads:
            t.join()
        self.assertEqual(reader_errors, [])

    def test_wall_and_monotonic_are_independent_on_rollback(self):
        clock = FakeClock(wall=1_000_000.0)
        mono_before = clock.monotonic()
        clock.set_wall(1.0)  # 墙上时间回拨近 12 天
        self.assertEqual(clock.monotonic(), mono_before)
        clock.set_wall(-5.0)
        self.assertEqual(clock.monotonic(), mono_before)


class SystemClockSanityTest(unittest.TestCase):
    def test_monotonic_non_decreasing_across_threads(self):
        # 真实 SystemClock：time.monotonic() 的跨线程基本保证；
        # 墙上时间仅做对照展示（可能不单调，故不断言它）。
        clock = SystemClock()
        stop = threading.Event()
        errors = []

        def reader():
            last = clock.monotonic()
            while not stop.is_set():
                cur = clock.monotonic()
                if cur < last:
                    errors.append((last, cur))
                    return
                last = cur

        threads = [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        stop.wait(0.2)
        stop.set()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class RollbackBehaviorTest(unittest.TestCase):
    """回拨场景下各逻辑的确定性行为说明，见 docs/CLOCKS.md。"""

    def test_timeout_survives_wall_rollback(self):
        clock = FakeClock(wall=10_000.0)
        clock.advance(4.0)  # 已过去 4s（wall 10004, mono 4）
        deadline_mono = 0.0
        self.assertAlmostEqual(
            timing.remaining(deadline_mono, 5.0, clock), 1.0)
        clock.set_wall(0.0)  # 墙上时间被回拨到原点
        self.assertAlmostEqual(
            timing.remaining(deadline_mono, 5.0, clock), 1.0)
        clock.advance(1.0)
        self.assertEqual(timing.remaining(deadline_mono, 5.0, clock), 0.0)

    def test_retry_deadline_survives_wall_rollback(self):
        # 重试的截止判定基于单调时间：回拨墙上时间不会让已超时的重试复活，
        # 也不会错误地提前放弃。
        clock = FakeClock()
        calls = {"n": 0}

        def op():
            calls["n"] += 1
            if calls["n"] == 1:
                clock.advance(4.5)
                raise RuntimeError("transient")
            if calls["n"] == 2:
                clock.set_wall(clock.wall() - 9_999.0)  # 回拨
                raise RuntimeError("still failing")
            return "recovered"

        with self.assertRaises(timing.DeadlineExpired):
            timing.retry(op, clock, max_attempts=5, base_delay=1.0,
                         max_delay=10.0, timeout=5.0)
        self.assertEqual(calls["n"], 1)  # 判定在第 1 次失败后立即放弃


if __name__ == "__main__":
    unittest.main()
