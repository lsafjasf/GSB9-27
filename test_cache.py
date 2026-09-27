"""复现用例 + 修复后回归测试。

运行：python3 -m unittest test_cache -v
"""

import threading
import time
import unittest

from cache_buggy import BuggyCache
from cache_fixed import FixedCache
from fake_source import FakeSource


def slow_refill_script(cache, source):
    """公共触发时序：
    T1 读线程 get(k) 未命中，开始回源，拿到 v1 的在途响应（阻塞在 gate）
    T2 主线程 set(k, 2)：写数据源 v2 -> 失效缓存
    T3 打开 gate，在途的 v1 响应返回并尝试写回缓存
    返回读线程拿到的值。
    """
    source.write("k", 1)
    source.gate = threading.Event()
    results = []
    t = threading.Thread(target=lambda: results.append(cache.get("k")))
    t.start()
    assert source.entered.wait(2), "回源未开始"
    cache.set("k", 2)
    source.gate.set()
    t.join(5)
    assert results, "读线程未返回"
    return results[0]


class BuggyReproTest(unittest.TestCase):
    """在未修复实现上稳定复现两个线上问题。"""

    def test_repro_stale_value_after_refill(self):
        source = FakeSource()
        cache = BuggyCache(source)
        slow_refill_script(cache, source)
        # 数据源已是 2，但慢回源把旧值 1 写回了缓存
        self.assertEqual(cache.get("k"), 1)
        self.assertEqual(source.read("k"), 2)

    def test_repro_empty_value_after_source_failure(self):
        source = FakeSource()
        source.write("k", 1)
        source.fail_next(IOError("source error"))
        cache = BuggyCache(source)
        self.assertIsNone(cache.get("k"))
        self.assertIsNone(cache.get("k"))
        self.assertEqual(source.read_count, 1)


class FixedCacheTest(unittest.TestCase):
    def test_no_stale_value_after_refill(self):
        source = FakeSource()
        cache = FixedCache(source)
        inflight_value = slow_refill_script(cache, source)
        # 在途读开始于写入之前，返回 v1 符合线性化；
        # 一致性断言：失效完成后的任何后续读取都不得返回更旧的值
        self.assertEqual(inflight_value, 1)
        self.assertEqual(cache.get("k"), 2)
        self.assertEqual(cache.get("k"), 2)

    def test_failure_propagates_and_is_not_cached(self):
        source = FakeSource()
        source.write("k", 1)
        source.fail_next(IOError("source error"))
        cache = FixedCache(source)
        with self.assertRaises(IOError):
            cache.get("k")
        self.assertEqual(cache.get("k"), 1)

    def test_timeout_is_transient(self):
        source = FakeSource()
        source.write("k", 1)
        source.fail_next(TimeoutError("source timeout"))
        cache = FixedCache(source)
        with self.assertRaises(TimeoutError):
            cache.get("k")
        self.assertEqual(cache.get("k"), 1)

    def test_singleflight(self):
        source = FakeSource()
        source.write("k", 7)
        source.gate = threading.Event()
        cache = FixedCache(source)
        results, errors = [], []

        def worker():
            try:
                results.append(cache.get("k"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        self.assertTrue(source.entered.wait(2))
        time.sleep(0.1)
        source.gate.set()
        for t in threads:
            t.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [7] * 10)
        self.assertEqual(source.read_count, 1)

    def test_read_after_write(self):
        source = FakeSource()
        cache = FixedCache(source)
        for i in range(200):
            cache.set("k", i)
            self.assertEqual(cache.get("k"), i)

    def test_concurrent_delete_and_write_consistency(self):
        """多线程压力：写线程单调递增，删除线程随机失效，读线程断言
        返回值不为空且不旧于读取开始前最后一次已完成的写入。"""
        source = FakeSource(latency=0.001)
        cache = FixedCache(source)
        cache.set("k", 0)
        lock = threading.Lock()
        committed = {"v": 0}
        stop = threading.Event()
        violations = []

        def writer():
            n = 0
            while not stop.is_set():
                n += 1
                cache.set("k", n)
                with lock:
                    committed["v"] = n
                time.sleep(0.0005)

        def deleter():
            while not stop.is_set():
                cache.delete("k")
                time.sleep(0.001)

        def reader():
            while not stop.is_set():
                with lock:
                    snap = committed["v"]
                value = cache.get("k")
                if value is None or value < snap:
                    violations.append((snap, value))

        threads = ([threading.Thread(target=writer), threading.Thread(target=deleter)]
                   + [threading.Thread(target=reader) for _ in range(6)])
        for t in threads:
            t.start()
        time.sleep(2.0)
        stop.set()
        for t in threads:
            t.join(5)
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
