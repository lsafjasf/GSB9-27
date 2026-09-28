import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from report_cache import DataRegistry, ReportCache


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self.now

    def advance(self, seconds):
        with self._lock:
            self.now += seconds


class ReportCacheTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.registry = DataRegistry(time_fn=self.clock)
        self.cache = ReportCache(self.registry, time_fn=self.clock)
        self.calls = {"a": 0, "b": 0}
        self.registry.set("orders", [1, 2, 3])
        self.registry.set("users", ["u1"])

        def compute_a(reg):
            self.calls["a"] += 1
            return sum(reg.get("orders"))

        def compute_b(reg):
            self.calls["b"] += 1
            return len(reg.get("users"))

        self.cache.register("report_a", ["orders"], compute_a)
        self.cache.register("report_b", ["users"], compute_b)

    def test_first_build_then_hit(self):
        self.assertEqual(self.cache.get("report_a"), 6)
        self.assertEqual(self.calls["a"], 1)
        self.assertEqual(self.cache.get("report_a"), 6)
        self.assertEqual(self.calls["a"], 1, "第二次读取应命中缓存")
        snap = self.cache.metrics.snapshot()
        self.assertEqual(snap["hits"], 1)
        self.assertEqual(snap["misses"], 1)
        self.assertEqual(snap["recomputes"], 1)
        self.assertEqual(snap["hit_rate"], 0.5)

    def test_version_invalidation_is_targeted_not_full_flush(self):
        self.assertEqual(self.cache.get("report_a"), 6)
        self.assertEqual(self.cache.get("report_b"), 1)
        # 底层数据 orders 变化
        self.clock.advance(2.0)
        self.registry.set("orders", [10, 20])
        # 受影响报表在下次读取时立即刷新（可接受时间 = 一次读取）
        self.assertEqual(self.cache.get("report_a"), 30)
        self.assertEqual(self.calls["a"], 2)
        # 不相关报表不得被全量清空波及
        self.assertEqual(self.cache.get("report_b"), 1)
        self.assertEqual(self.calls["b"], 1, "report_b 不应因 orders 变化而重算")
        snap = self.cache.metrics.snapshot()
        self.assertEqual(snap["recomputes"], 3)  # 两次首次构建 + 一次失效刷新
        self.assertAlmostEqual(snap["avg_refresh_lag"], 0.0)  # 变更后尚未被读取即无滞后

    def test_refresh_lag_measured(self):
        self.cache.get("report_a")
        self.clock.advance(5.0)
        self.registry.set("orders", [100])
        self.clock.advance(3.0)  # 变更后 3 秒才有读者触发刷新
        self.assertEqual(self.cache.get("report_a"), 100)
        snap = self.cache.metrics.snapshot()
        self.assertAlmostEqual(snap["avg_refresh_lag"], 3.0)

    def test_frequent_dependency_changes(self):
        self.cache.get("report_a")
        for i in range(10):
            self.registry.set("orders", [i])
            self.assertEqual(self.cache.get("report_a"), i)
        self.assertEqual(self.calls["a"], 11, "每次变更后的首次读取重算一次，不多不少")
        # 无变更时连续读取全部命中
        for _ in range(20):
            self.cache.get("report_a")
        self.assertEqual(self.calls["a"], 11)
        snap = self.cache.metrics.snapshot()
        self.assertEqual(snap["recomputes"], 11)
        self.assertEqual(snap["hits"], 20)

    def test_recompute_failure_keeps_stale_value(self):
        self.assertEqual(self.cache.get("report_a"), 6)
        self.registry.set("orders", [7, 8])

        def broken(reg):
            self.calls["a"] += 1
            raise RuntimeError("db down")

        self.cache.register("report_a", ["orders"], broken)
        # 重算失败：返回旧值而不是抛错
        self.assertEqual(self.cache.get("report_a"), 6)
        snap = self.cache.metrics.snapshot()
        self.assertEqual(snap["recompute_failures"], 1)
        self.assertEqual(snap["stale_serves"], 1)
        # 冷却期内直接回退旧值，不重复打挂掉的下游
        self.assertEqual(self.cache.get("report_a"), 6)
        self.assertEqual(self.cache.metrics.snapshot()["recompute_failures"], 1)
        # 冷却期过后且依赖恢复，正常刷新
        self.clock.advance(2.0)
        self.cache.register(
            "report_a", ["orders"], lambda reg: sum(reg.get("orders"))
        )
        self.assertEqual(self.cache.get("report_a"), 15)

    def test_first_build_failure_without_old_value_raises(self):
        def broken(reg):
            raise RuntimeError("db down")

        self.cache.register("report_c", ["orders"], broken)
        with self.assertRaises(RuntimeError):
            self.cache.get("report_c")


class ConcurrencyTest(unittest.TestCase):
    def test_write_during_recompute_is_not_cached_under_new_version(self):
        """低速重算期间依赖被写入：旧值绝不能盖上新版本号长期命中。"""
        registry = DataRegistry()
        registry.set("orders", [1, 2, 3])
        cache = ReportCache(registry)
        compute_started = threading.Event()
        release_compute = threading.Event()
        calls = {"n": 0}

        def slow_compute(reg):
            calls["n"] += 1
            total = sum(reg.get("orders"))  # 进入重算后先按当前数据取值
            if calls["n"] == 2:  # 只让「失效后的重算」变慢
                compute_started.set()
                self.assertTrue(release_compute.wait(timeout=5))
            return total

        cache.register("r", ["orders"], slow_compute)
        self.assertEqual(cache.get("r"), 6)

        # 失效后触发低速重算，并在重算进行中写入新版本
        registry.set("orders", [10, 20])
        reader_done = threading.Event()
        reader_result = {}

        def reader():
            reader_result["value"] = cache.get("r")
            reader_done.set()

        thread = threading.Thread(target=reader)
        thread.start()
        self.assertTrue(compute_started.wait(timeout=5))
        registry.set("orders", [100])  # 重算期间落进来的底层写入
        release_compute.set()
        self.assertTrue(reader_done.wait(timeout=5))
        thread.join(timeout=5)

        self.assertEqual(
            reader_result["value"], 100,
            "重算期间数据已变更，必须放弃与旧数据对应的结果并重试刷新",
        )
        # 修复前：条目会被打成最新版本号却存着旧值，之后每次读取都陈旧命中
        self.assertEqual(cache.get("r"), 100)
        snap = cache.metrics.snapshot()
        self.assertEqual(snap["recomputes"], 3, "首次构建 + 冲突重算 + 重试各一次")
        hits_before = snap["hits"]
        for _ in range(5):
            self.assertEqual(cache.get("r"), 100)
        self.assertEqual(
            cache.metrics.snapshot()["hits"], hits_before + 5,
            "新版本条目应正常命中，而不是存着陈旧值反复骗过校验",
        )

    def test_hot_key_concurrent_misses_collapse_to_one_recompute(self):
        registry = DataRegistry()
        registry.set("orders", [1, 2, 3])
        cache = ReportCache(registry)
        compute_calls = []
        compute_started = threading.Event()
        release_compute = threading.Event()
        lock = threading.Lock()

        def compute(reg):
            with lock:
                compute_calls.append(1)
            compute_started.set()
            release_compute.wait(timeout=5)
            return sum(reg.get("orders"))

        cache.register("hot", ["orders"], compute)

        n_threads = 16
        results = [None] * n_threads
        errors = [None] * n_threads
        barrier = threading.Barrier(n_threads)

        def worker(i):
            try:
                barrier.wait(timeout=5)
                results[i] = cache.get("hot")
            except Exception as exc:  # noqa: BLE001
                errors[i] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        # 等领导者进入重算后，给跟随者时间全部阻塞在 single-flight 上
        self.assertTrue(compute_started.wait(timeout=5))
        time.sleep(0.2)
        release_compute.set()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [None] * n_threads)
        self.assertEqual(results, [6] * n_threads)
        self.assertEqual(
            len(compute_calls), 1,
            "并发未命中必须合并为一次重算，避免失效风暴",
        )
        snap = cache.metrics.snapshot()
        self.assertEqual(snap["recomputes"], 1)

    def test_concurrent_reads_after_invalidation_collapse(self):
        registry = DataRegistry()
        registry.set("orders", [1])
        cache = ReportCache(registry)
        calls = []
        lock = threading.Lock()

        def compute(reg):
            with lock:
                calls.append(1)
            time.sleep(0.05)
            return sum(reg.get("orders"))

        cache.register("r", ["orders"], compute)
        self.assertEqual(cache.get("r"), 1)
        registry.set("orders", [5, 5])

        results = []
        def worker():
            results.append(cache.get("r"))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(results, [10] * 8)
        self.assertEqual(len(calls), 2, "失效后的并发读取同样只重算一次")

    def test_concurrent_failure_falls_back_to_stale_for_all(self):
        clock = FakeClock()
        registry = DataRegistry(time_fn=clock)
        registry.set("orders", [1])
        cache = ReportCache(registry, time_fn=clock)
        state = {"fail": False}
        lock = threading.Lock()

        def compute(reg):
            with lock:
                if state["fail"]:
                    raise RuntimeError("db down")
            time.sleep(0.05)
            return sum(reg.get("orders"))

        cache.register("r", ["orders"], compute)
        self.assertEqual(cache.get("r"), 1)
        clock.advance(1.0)
        registry.set("orders", [2])
        with lock:
            state["fail"] = True

        results, errors = [], []
        def worker():
            try:
                results.append(cache.get("r"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(results, [1] * 8, "所有并发读者都应拿到旧值而非异常")
        self.assertEqual(cache.metrics.snapshot()["recompute_failures"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
