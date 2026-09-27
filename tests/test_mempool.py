"""内存池回归测试。

运行: python3 -m unittest discover -s tests -t . -v

覆盖：
- 尺寸分档取整
- 释放时相邻空闲块合并（左/右/两侧）
- OOM 与释放后恢复
- double free 防护
- 复现场景在修复后不再失败（回归锁）
- 朴素池在复现场景下失败（问题存档）
- 并发分配/回收的不变量断言：
    * 已分配块的内容在整个生命周期内不被改写（不被移动/复用/释放）
    * 已分配块的 (offset, size) 不变
    * 池内部账务不变量（check_invariants）在并发下持续成立
    * 全部释放后空闲块合并回一整块（碎片率回到 1.0）
"""

import os
import random
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.mempool_naive import NaivePool, OutOfMemoryError as NaiveOOM
from src.mempool_fixed import (
    MemoryPool,
    OutOfMemoryError as FixedOOM,
    class_size,
)
import reproduce


class TestClassSize(unittest.TestCase):
    def test_rounding(self):
        self.assertEqual(class_size(1), 16)
        self.assertEqual(class_size(16), 16)
        self.assertEqual(class_size(17), 32)
        self.assertEqual(class_size(100), 128)
        self.assertEqual(class_size(4096), 4096)
        self.assertEqual(class_size(4097), 8192)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            class_size(0)


class TestCoalescing(unittest.TestCase):
    def test_merge_left_right_and_both(self):
        pool = MemoryPool(1024)
        a = pool.alloc(64)
        b = pool.alloc(64)
        c = pool.alloc(64)
        d = pool.alloc(64)
        b.free()
        self.assertEqual(pool._free, [(64, 64), (256, 768)])
        c.free()
        self.assertEqual(pool._free, [(64, 128), (256, 768)])
        a.free()
        self.assertEqual(pool._free, [(0, 192), (256, 768)])
        d.free()
        self.assertEqual(pool._free, [(0, 1024)])
        self.assertEqual(pool.stats().fragmentation, 1.0)

    def test_full_cycle_returns_whole_pool(self):
        pool = MemoryPool(1 << 16)
        rng = random.Random(7)
        live = []
        for _ in range(3000):
            if live and (len(live) >= 10 or rng.random() < 0.5):
                live.pop(rng.randrange(len(live))).free()
            else:
                try:
                    live.append(pool.alloc(rng.choice([8, 30, 100, 900, 5000])))
                except FixedOOM:
                    pass
            if rng.random() < 0.05:
                pool.check_invariants()
        for blk in live:
            blk.free()
        pool.check_invariants()
        self.assertEqual(pool._free, [(0, 1 << 16)])


class TestOOM(unittest.TestCase):
    def test_oom_and_recovery(self):
        pool = MemoryPool(4096)
        big = pool.alloc(4096)
        with self.assertRaises(FixedOOM):
            pool.alloc(16)
        big.free()
        again = pool.alloc(4096)
        again.free()

    def test_double_free_raises(self):
        pool = MemoryPool(1024)
        blk = pool.alloc(16)
        blk.free()
        with self.assertRaises(RuntimeError):
            blk.free()


class TestReproduceScenario(unittest.TestCase):
    """把 reproduce.py 的同一份确定性序列同时打到两个池上。"""

    def _drive(self, pool, oom_exc):
        live = []
        for kind, arg in reproduce.make_sequence():
            if kind == "alloc":
                try:
                    live.append(pool.alloc(arg))
                except oom_exc:
                    pass
            elif live:
                live.pop(arg % len(live)).free()
        return pool

    def test_naive_pool_fails_probe(self):
        """问题存档：朴素池总空闲 ~1MB 却分不出 64KiB。"""
        pool = self._drive(NaivePool(reproduce.CAPACITY), NaiveOOM)
        s = pool.stats()
        self.assertGreater(s.total_free, reproduce.PROBE * 4)
        with self.assertRaises(NaiveOOM):
            pool.alloc(reproduce.PROBE)

    def test_fixed_pool_passes_probe(self):
        """回归锁：修复后同一场景必须分配成功，且碎片率保持高位。"""
        pool = self._drive(MemoryPool(reproduce.CAPACITY), FixedOOM)
        blk = pool.alloc(reproduce.PROBE)
        blk.free()
        self.assertGreater(pool.stats().fragmentation, 0.3)


class TestConcurrency(unittest.TestCase):
    THREADS = 8
    OPS_PER_THREAD = 4000
    MAX_LIVE_PER_THREAD = 40

    def test_concurrent_alloc_free_invariants(self):
        pool = MemoryPool(4 << 20)
        stop = threading.Event()
        errors = []

        def auditor():
            # 并发“整理”期间持续断言内部不变量
            while not stop.is_set():
                try:
                    pool.check_invariants()
                except AssertionError as e:
                    errors.append(f"invariant violated: {e}")
                    return

        def worker(tid):
            rng = random.Random(1000 + tid)
            live = []
            try:
                for seq in range(self.OPS_PER_THREAD):
                    if live and (len(live) >= self.MAX_LIVE_PER_THREAD
                                 or rng.random() < 0.45):
                        blk, token, off, sz = live.pop(rng.randrange(len(live)))
                        # 不变量：已分配块不被移动
                        assert blk.offset == off and blk.size == sz, (
                            f"block moved: ({blk.offset},{blk.size}) != ({off},{sz})"
                        )
                        # 不变量：已分配块内容不被改写（未被复用/释放/整理）
                        view = blk.view()
                        expected = (token.to_bytes(2, "little")
                                    * (len(view) // 2 + 1))[: len(view)]
                        assert bytes(view) == expected, (
                            f"live block content corrupted (tid={tid} seq={seq})"
                        )
                        blk.free()
                    else:
                        size = rng.choice([16, 24, 64, 200, 777, 1500, 2048])
                        try:
                            blk = pool.alloc(size)
                        except FixedOOM:
                            continue
                        token = (tid << 13 | seq) & 0xFFFF or 1
                        view = blk.view()
                        view[:] = (token.to_bytes(2, "little")
                                   * (len(view) // 2 + 1))[: len(view)]
                        live.append((blk, token, blk.offset, blk.size))
            except AssertionError as e:
                errors.append(str(e))
            finally:
                for blk, _token, _off, _sz in live:
                    blk.free()

        audit = threading.Thread(target=auditor)
        audit.start()
        threads = [threading.Thread(target=worker, args=(t,))
                   for t in range(self.THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stop.set()
        audit.join()

        self.assertEqual(errors, [])
        pool.check_invariants()
        # 全部释放后：合并回一整块，碎片率回到 1.0
        s = pool.stats()
        self.assertEqual(s.total_free, pool.capacity)
        self.assertEqual(s.max_block, pool.capacity)
        self.assertEqual(s.fragmentation, 1.0)


if __name__ == "__main__":
    unittest.main()
