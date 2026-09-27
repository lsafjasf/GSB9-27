"""uidgen 的自测（仅标准库 unittest）。

运行：python3 -m unittest -v        或        python3 test_uidgen.py

覆盖：
- 标识结构（时间部分 + 机器部分 + 序列部分）与趋势递增；
- 同一毫秒序列耗尽 -> 确定行为：等待下一毫秒；
- 时钟源注入固定值、时间前跳、小幅/大幅回拨、等待期间回拨扩大；
- 机器位 / 机器号等非法配置；
- 并发唯一性：固定时钟下多线程同一毫秒生成 10 万个标识，集合大小 == 生成次数；
  以及真实时钟下 8 线程突发 10 万个标识的唯一性。
"""

import threading
import unittest

from uidgen import (
    DEFAULT_EPOCH_MS,
    ClockRolledBackError,
    IdGenerator,
    InvalidConfigError,
    TimestampOverflowError,
)


class FakeClock:
    """固定值假时钟：clock() 恒等于当前值；sleep() 每次把时钟拨快 1 毫秒。"""

    def __init__(self, start_ms):
        self.now_ms = start_ms
        self.sleep_calls = 0

    def clock(self):
        return self.now_ms

    def sleep(self, _milliseconds):
        self.sleep_calls += 1
        self.now_ms += 1


def make_generator(clock, **overrides):
    kwargs = {
        "machine_id": 3,
        "epoch_ms": DEFAULT_EPOCH_MS,
        "max_backward_ms": 5,
        "clock_ms": clock.clock,
        "sleep_ms": clock.sleep,
    }
    kwargs.update(overrides)
    return IdGenerator(**kwargs)


class StructureTest(unittest.TestCase):
    def test_ids_are_positive_63bit_and_strictly_increasing(self):
        clock = FakeClock(DEFAULT_EPOCH_MS + 1000)
        gen = make_generator(clock)
        ids = [gen.next_id() for _ in range(2000)]
        for identifier in ids:
            self.assertGreater(identifier, 0)
            self.assertLess(identifier, 1 << 63)
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), len(ids))

    def test_decode_recovers_time_machine_and_sequence_parts(self):
        start = DEFAULT_EPOCH_MS + 123456
        clock = FakeClock(start)
        gen = make_generator(clock, machine_id=17)
        for expected_seq in range(10):
            timestamp_ms, machine, sequence = gen.decode(gen.next_id())
            self.assertEqual(timestamp_ms, start)
            self.assertEqual(machine, 17)
            self.assertEqual(sequence, expected_seq)

    def test_machine_part_separates_ids_across_machines(self):
        start = DEFAULT_EPOCH_MS + 50
        clock_a, clock_b = FakeClock(start), FakeClock(start)
        gen_a = make_generator(clock_a, machine_id=1)
        gen_b = make_generator(clock_b, machine_id=2)
        ids_a = {gen_a.next_id() for _ in range(1000)}
        ids_b = {gen_b.next_id() for _ in range(1000)}
        self.assertTrue(ids_a.isdisjoint(ids_b))
        sample = next(iter(ids_b))
        self.assertEqual(gen_b.decode(sample)[1], 2)


class SequenceExhaustionTest(unittest.TestCase):
    def test_exhaustion_waits_for_next_millisecond(self):
        clock = FakeClock(DEFAULT_EPOCH_MS + 7)
        gen = make_generator(clock, timestamp_bits=50, machine_bits=5)  # 序列位 8，容量 256
        capacity = gen.max_sequence + 1
        self.assertEqual(capacity, 256)

        ids = [gen.next_id() for _ in range(capacity + 2)]
        self.assertEqual(len(set(ids)), capacity + 2)

        first_ms, _, first_seq = gen.decode(ids[0])
        last_in_ms, _, last_seq = gen.decode(ids[capacity - 1])
        overflow_ms, _, overflow_seq = gen.decode(ids[capacity])
        self.assertEqual(first_ms, last_in_ms)
        self.assertEqual((first_seq, last_seq), (0, capacity - 1))
        self.assertEqual(overflow_ms, first_ms + 1)  # 确定行为：等到下一毫秒
        self.assertEqual(overflow_seq, 0)
        self.assertGreaterEqual(clock.sleep_calls, 1)  # 确实发生了等待


class ClockBackwardTest(unittest.TestCase):
    def test_small_backward_waits_until_clock_catches_up(self):
        clock = FakeClock(DEFAULT_EPOCH_MS + 1000)
        gen = make_generator(clock)
        first = gen.next_id()
        clock.now_ms -= 3  # 回拨 3ms，未超过容忍上限 5ms
        second = gen.next_id()
        self.assertGreater(second, first)
        self.assertGreaterEqual(clock.sleep_calls, 3)  # 等待时钟追平
        # 追平后在原毫秒上继续递增序列，不会倒退也不会重复
        self.assertEqual(gen.decode(second)[0], DEFAULT_EPOCH_MS + 1000)

    def test_large_backward_is_rejected_with_deviation(self):
        clock = FakeClock(DEFAULT_EPOCH_MS + 1000)
        gen = make_generator(clock)
        first = gen.next_id()
        clock.now_ms -= 100  # 回拨 100ms，超过容忍上限 5ms
        with self.assertRaises(ClockRolledBackError) as ctx:
            gen.next_id()
        self.assertEqual(ctx.exception.backward_ms, 100)
        self.assertEqual(ctx.exception.last_ms, DEFAULT_EPOCH_MS + 1000)
        self.assertEqual(ctx.exception.now_ms, DEFAULT_EPOCH_MS + 900)
        clock.now_ms += 100  # 时钟恢复后仍可继续生成，且不重复不倒退
        self.assertGreater(gen.next_id(), first)

    def test_backward_growing_beyond_tolerance_during_wait_is_rejected(self):
        clock = FakeClock(DEFAULT_EPOCH_MS + 1000)
        gen = make_generator(clock)
        gen.next_id()
        clock.now_ms -= 2  # 先小幅回拨，进入等待

        original_sleep = clock.sleep

        def drifting_sleep(milliseconds):
            original_sleep(milliseconds)
            clock.now_ms -= 2  # 等待期间时钟继续倒退，偏差逐步扩大

        gen._sleep_ms = drifting_sleep
        with self.assertRaises(ClockRolledBackError) as ctx:
            gen.next_id()
        self.assertGreater(ctx.exception.backward_ms, 5)


class ClockJumpTest(unittest.TestCase):
    def test_forward_jump_produces_larger_ids(self):
        clock = FakeClock(DEFAULT_EPOCH_MS + 1000)
        gen = make_generator(clock)
        before = gen.next_id()
        clock.now_ms += 60_000  # 时间前跳一分钟
        after = gen.next_id()
        self.assertGreater(after, before)
        self.assertEqual(gen.decode(after)[0], DEFAULT_EPOCH_MS + 61_000)
        self.assertEqual(gen.decode(after)[2], 0)  # 新毫秒序列归零

    def test_fixed_injected_clock_gives_deterministic_ids(self):
        clock = FakeClock(DEFAULT_EPOCH_MS + 42)
        gen = make_generator(clock, machine_id=9)
        expected = (42 << (gen.sequence_bits + 5)) | (9 << gen.sequence_bits)
        self.assertEqual(gen.next_id(), expected)
        self.assertEqual(gen.next_id(), expected + 1)


class InvalidConfigTest(unittest.TestCase):
    def test_invalid_machine_bits_layouts(self):
        with self.assertRaises(InvalidConfigError):
            IdGenerator(0, timestamp_bits=41, machine_bits=22)  # 序列位 = 0
        with self.assertRaises(InvalidConfigError):
            IdGenerator(0, timestamp_bits=41, machine_bits=23)  # 序列位为负
        with self.assertRaises(InvalidConfigError):
            IdGenerator(0, timestamp_bits=0, machine_bits=5)    # 时间位为 0
        with self.assertRaises(InvalidConfigError):
            IdGenerator(0, timestamp_bits=41, machine_bits=-1)  # 机器位为负

    def test_invalid_machine_id(self):
        with self.assertRaises(InvalidConfigError):
            IdGenerator(32, machine_bits=5)   # 5 位机器号最大 31
        with self.assertRaises(InvalidConfigError):
            IdGenerator(-1, machine_bits=5)   # 负数机器号
        with self.assertRaises(InvalidConfigError):
            IdGenerator(1.5, machine_bits=5)  # 非整数机器号

    def test_other_invalid_parameters(self):
        with self.assertRaises(InvalidConfigError):
            IdGenerator(0, max_backward_ms=-1)
        with self.assertRaises(InvalidConfigError):
            IdGenerator(0, poll_interval_ms=0)

    def test_timestamp_overflow(self):
        clock = FakeClock(DEFAULT_EPOCH_MS + 16)
        gen = make_generator(clock, timestamp_bits=4, machine_bits=5)
        with self.assertRaises(TimestampOverflowError):
            gen.next_id()

    def test_clock_before_epoch(self):
        clock = FakeClock(DEFAULT_EPOCH_MS - 1)
        gen = make_generator(clock)
        with self.assertRaises(ValueError):
            gen.next_id()

    def test_from_env(self):
        import os

        os.environ["UIDGEN_TEST_MACHINE"] = "7"
        try:
            gen = IdGenerator.from_env(env_var="UIDGEN_TEST_MACHINE")
            self.assertEqual(gen.machine_id, 7)
        finally:
            del os.environ["UIDGEN_TEST_MACHINE"]
        with self.assertRaises(InvalidConfigError):
            IdGenerator.from_env(env_var="UIDGEN_TEST_MACHINE_MISSING")


class ConcurrencyUniquenessTest(unittest.TestCase):
    TOTAL = 100_000

    def _generate_in_threads(self, gen, thread_count):
        barrier = threading.Barrier(thread_count)
        results = []
        results_lock = threading.Lock()
        chunk = self.TOTAL // thread_count

        def worker():
            local = []
            barrier.wait()
            for _ in range(chunk):
                local.append(gen.next_id())
            with results_lock:
                results.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results

    def test_same_millisecond_100k_ids_are_unique(self):
        """固定时钟注入：16 线程在同一毫秒内生成 10 万个标识，集合大小必须等于生成次数。"""
        clock = FakeClock(DEFAULT_EPOCH_MS + 1000)
        gen = make_generator(clock)  # 默认布局每毫秒容量 131072 > 100000
        ids = self._generate_in_threads(gen, thread_count=16)
        self.assertEqual(len(ids), self.TOTAL)
        self.assertEqual(len(set(ids)), self.TOTAL)
        for identifier in ids:
            timestamp_ms, machine, _ = gen.decode(identifier)
            self.assertEqual(timestamp_ms, DEFAULT_EPOCH_MS + 1000)
            self.assertEqual(machine, 3)
        sequences = {gen.decode(identifier)[2] for identifier in ids}
        self.assertEqual(sequences, set(range(self.TOTAL)))  # 序列 0..99999 各出现一次

    def test_real_clock_burst_100k_ids_are_unique(self):
        """真实时钟：8 线程突发 10 万个标识，验证等待逻辑下集合大小仍等于生成次数。"""
        gen = IdGenerator(machine_id=1)
        ids = self._generate_in_threads(gen, thread_count=8)
        self.assertEqual(len(ids), self.TOTAL)
        self.assertEqual(len(set(ids)), self.TOTAL)
        for identifier in ids:
            self.assertEqual(gen.decode(identifier)[1], 1)


if __name__ == "__main__":
    unittest.main()
