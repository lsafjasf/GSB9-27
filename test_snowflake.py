"""snowflake.py 自测：python3 test_snowflake.py -v"""

import threading
import unittest

from snowflake import (
    EPOCH,
    MAX_SEQUENCE,
    ClockMovedBackwardsError,
    SnowflakeGenerator,
)


class FakeClock:
    """手动推进的时钟。"""

    def __init__(self, start=EPOCH + 1000):
        self.t = start

    def __call__(self):
        return self.t

    def set(self, t):
        self.t = t

    def advance(self, ms):
        self.t += ms


class ScriptClock:
    """按脚本返回值，用完后重复最后一个值。用于确定性地走等待路径。"""

    def __init__(self, values):
        self.values = list(values)
        self.i = 0

    def __call__(self):
        v = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return v


class StepClock:
    """每 calls_per_ms 次调用前进 1ms，线程安全。用于并发压测。"""

    def __init__(self, start=EPOCH + 1000, calls_per_ms=10_000):
        self.start = start
        self.calls_per_ms = calls_per_ms
        self.n = 0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.n += 1
            return self.start + self.n // self.calls_per_ms


class TestStructure(unittest.TestCase):
    def test_fields_roundtrip(self):
        clock = FakeClock()
        gen = SnowflakeGenerator(machine_id=42, clock=clock)
        id1 = gen.next_id()
        parts = SnowflakeGenerator.parse(id1)
        self.assertEqual(parts["timestamp_ms"], clock.t)
        self.assertEqual(parts["machine_id"], 42)
        self.assertEqual(parts["sequence"], 0)

    def test_monotonic_increasing(self):
        clock = FakeClock()
        gen = SnowflakeGenerator(machine_id=1, clock=clock)
        ids = [gen.next_id() for _ in range(1000)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 1000)

    def test_machine_ids_disjoint(self):
        clock = FakeClock()
        g1 = SnowflakeGenerator(machine_id=1, clock=clock)
        g2 = SnowflakeGenerator(machine_id=2, clock=clock)
        ids1 = {g1.next_id() for _ in range(100)}
        ids2 = {g2.next_id() for _ in range(100)}
        self.assertTrue(ids1.isdisjoint(ids2))


class TestSequenceExhaustion(unittest.TestCase):
    def test_exhaustion_waits_for_next_ms(self):
        # 固定时钟下生成满一个序列空间（4096 个），全部落在同一毫秒
        clock = FakeClock()
        gen = SnowflakeGenerator(machine_id=0, clock=clock)
        ids = [gen.next_id() for _ in range(MAX_SEQUENCE + 1)]
        self.assertEqual(len(set(ids)), MAX_SEQUENCE + 1)
        seqs = [SnowflakeGenerator.parse(i)["sequence"] for i in ids]
        self.assertEqual(seqs, list(range(MAX_SEQUENCE + 1)))

        # 第 4097 个会触发等待；用脚本时钟让等待后时钟前进 1ms
        gen2 = SnowflakeGenerator(
            machine_id=0,
            clock=ScriptClock([EPOCH + 1000] * (MAX_SEQUENCE + 1)
                              + [EPOCH + 1000, EPOCH + 1001]),
        )
        for _ in range(MAX_SEQUENCE + 1):
            gen2.next_id()
        overflow_id = gen2.next_id()
        parts = SnowflakeGenerator.parse(overflow_id)
        self.assertEqual(parts["timestamp_ms"], EPOCH + 1001)
        self.assertEqual(parts["sequence"], 0)


class TestClockRollback(unittest.TestCase):
    def test_small_rollback_waits(self):
        # t=1000 生成后回拨 3ms（<= 默认容忍 5ms），等待到时钟走到 1001
        clock = ScriptClock([EPOCH + 1000, EPOCH + 997, EPOCH + 1001])
        gen = SnowflakeGenerator(machine_id=0, clock=clock)
        first = gen.next_id()
        second = gen.next_id()
        self.assertGreater(second, first)
        self.assertEqual(
            SnowflakeGenerator.parse(second)["timestamp_ms"], EPOCH + 1001
        )

    def test_large_rollback_raises_with_offset(self):
        clock = FakeClock(EPOCH + 10_000)
        gen = SnowflakeGenerator(machine_id=0, clock=clock)
        gen.next_id()
        clock.set(EPOCH + 9_000)  # 回拨 1000ms
        with self.assertRaises(ClockMovedBackwardsError) as ctx:
            gen.next_id()
        self.assertEqual(ctx.exception.offset_ms, 1000)

    def test_rollback_never_yields_duplicate_or_regression(self):
        # 回拨拒绝后恢复时钟，已产出的 ID 序列仍然严格递增且无重复
        clock = FakeClock(EPOCH + 10_000)
        gen = SnowflakeGenerator(machine_id=0, clock=clock)
        produced = [gen.next_id() for _ in range(100)]
        clock.set(EPOCH + 1_000)
        for _ in range(10):
            with self.assertRaises(ClockMovedBackwardsError):
                gen.next_id()
        clock.set(EPOCH + 10_001)
        produced.append(gen.next_id())
        self.assertEqual(produced, sorted(produced))
        self.assertEqual(len(set(produced)), len(produced))


class TestClockJump(unittest.TestCase):
    def test_forward_jump_resets_sequence(self):
        clock = FakeClock(EPOCH + 1000)
        gen = SnowflakeGenerator(machine_id=7, clock=clock)
        gen.next_id()
        clock.set(EPOCH + 999_999)  # 大幅向前跳跃
        jumped = gen.next_id()
        parts = SnowflakeGenerator.parse(jumped)
        self.assertEqual(parts["timestamp_ms"], EPOCH + 999_999)
        self.assertEqual(parts["sequence"], 0)


class TestMachineIdValidation(unittest.TestCase):
    def test_invalid_machine_ids(self):
        for bad in (-1, 1024, 10_000, "3", 1.5, None, True):
            with self.assertRaises((ValueError, TypeError), msg=f"machine_id={bad!r}"):
                SnowflakeGenerator(machine_id=bad)

    def test_boundary_machine_ids_ok(self):
        SnowflakeGenerator(machine_id=0)
        SnowflakeGenerator(machine_id=1023)


class TestConcurrentUniqueness(unittest.TestCase):
    def test_100k_ids_multithreaded_unique(self):
        # 8 线程共 100_000 个 ID；StepClock 让大量 ID 落在同一虚拟毫秒，
        # 同时保证序列耗尽后的等待路径能够推进。
        clock = StepClock(calls_per_ms=10_000)
        gen = SnowflakeGenerator(machine_id=3, clock=clock)
        total = 100_000
        n_threads = 8
        per_thread = total // n_threads
        results = [[] for _ in range(n_threads)]

        def worker(idx):
            out = results[idx]
            for _ in range(per_thread):
                out.append(gen.next_id())

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        all_ids = [i for chunk in results for i in chunk]
        self.assertEqual(len(all_ids), total)
        self.assertEqual(len(set(all_ids)), total)  # 核心断言：集合大小 == 生成次数
        self.assertEqual(sorted(all_ids), sorted(set(all_ids)))


if __name__ == "__main__":
    unittest.main()
