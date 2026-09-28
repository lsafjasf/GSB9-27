"""生命周期库测试：可见性、分批清理、并发安全、边界情形。

运行：python3 -m unittest tests.test_lifecycle -v
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lifecycle import (
    FakeClock,
    IntervalSweeper,
    KeyExpiredError,
    LifecycleStore,
)


class VisibilityTest(unittest.TestCase):
    """过期对象立即不可见（读取路径必须判定）。"""

    def test_get_and_contains_check_expiry(self):
        clock = FakeClock(100)
        store = LifecycleStore(clock=clock)
        store.put("a", "value-a", ttl=10, size=10)
        self.assertEqual(store.get("a"), "value-a")
        self.assertIn("a", store)

        clock.advance(10)  # expires_at == now：过期（<= 判定）
        self.assertNotIn("a", store)
        with self.assertRaises(KeyExpiredError):
            store.begin_access("a")
        # 物理条目仍在（惰性），但逻辑上不可见
        self.assertEqual(store.stats().live_objects, 1)
        self.assertEqual(store.stats().visible_objects, 0)
        self.assertIsNone(store.get("a"))
        self.assertEqual(store.get("a", "gone"), "gone")

    def test_missing_key(self):
        store = LifecycleStore(clock=FakeClock(0))
        with self.assertRaises(KeyError):
            store.begin_access("nope")
        with self.assertRaises(KeyError):
            store.renew("nope", 10)
        self.assertFalse(store.delete("nope"))

    def test_zero_ttl_expires_immediately(self):
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        store.put("z", 1, ttl=0, size=1)
        self.assertNotIn("z", store)
        r = store.sweep_batch(10)
        self.assertEqual(r.reclaimed, 1)

    def test_negative_ttl_rejected(self):
        store = LifecycleStore(clock=FakeClock(0))
        with self.assertRaises(ValueError):
            store.put("x", 1, ttl=-1)
        with self.assertRaises(ValueError):
            store.renew("x", -1)


class SweepBatchTest(unittest.TestCase):
    def test_budget_is_work_upper_bound(self):
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        for i in range(100):
            store.put(i, i, ttl=10, size=1)
        clock.advance(11)
        r1 = store.sweep_batch(budget=10)
        self.assertLessEqual(r1.inspected, 10)
        self.assertEqual(r1.reclaimed, 10)
        r2 = store.sweep_batch(budget=10)
        self.assertEqual(r2.reclaimed, 10)
        self.assertEqual(store.stats().live_objects, 80)

    def test_early_stop_when_nothing_expired(self):
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        for i, ttl in enumerate([5, 6, 7]):
            store.put(i, i, ttl=ttl, size=1)
        r = store.sweep_batch(budget=1000)
        self.assertTrue(r.early_stop)
        self.assertEqual(r.inspected, 1)  # 看了堆顶即停止，没有全量扫描
        self.assertEqual(r.reclaimed, 0)

    def test_mixed_expiry_order(self):
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        store.put("late", 1, ttl=100, size=10)
        store.put("early", 2, ttl=1, size=20)
        clock.advance(2)
        r = store.sweep_batch(100)
        self.assertEqual(r.reclaimed, 1)
        self.assertEqual(r.reclaimed_bytes, 20)
        self.assertEqual(r.skipped_not_expired, 1)
        self.assertTrue(r.early_stop)
        self.assertEqual(store.stats().live_bytes, 10)
        self.assertIn("late", store)

    def test_reclaimed_bytes_accounting(self):
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        store.put("a", "x", ttl=1, size=123)
        store.put("b", "y", ttl=1, size=456)
        clock.advance(2)
        r = store.sweep_batch(10)
        self.assertEqual(r.reclaimed_bytes, 579)
        self.assertEqual(store.stats().total_reclaimed_bytes, 579)


class RenewalAndStaleHeapTest(unittest.TestCase):
    def test_repeated_renewal_keeps_object_alive(self):
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        store.put("k", "v", ttl=10, size=1)
        for t in range(100):
            clock.set(float(t))
            expires = store.renew("k", ttl=10)
            self.assertEqual(expires, float(t + 10))
            self.assertIn("k", store)
        # 过期前：清理什么都不回收，但会丢弃历史续期遗留的旧堆项吗？
        # 旧堆项 expires_at 都在未来，因此只能看到堆顶即提前停止。
        r = store.sweep_batch(1000)
        self.assertTrue(r.early_stop)
        self.assertEqual(r.reclaimed, 0)
        self.assertEqual(store.stats().live_objects, 1)

    def test_stale_heap_items_discarded_after_final_expiry(self):
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        store.put("k", "v", ttl=10, size=1)
        for t in range(20):
            clock.set(float(t))
            store.renew("k", ttl=10)
        # 最后一次续期到 t=120；跳到 121，全部历史堆项过期
        clock.set(121)
        results = store.sweep_drain(1000)
        stats = store.stats()
        self.assertEqual(stats.live_objects, 0)
        # 21 条堆项（1 次 put + 20 次 renew）：1 条触发物理删除，20 条墓碑
        total_stale = sum(r.stale_heap_items for r in results)
        total_reclaimed = sum(r.reclaimed for r in results)
        self.assertEqual(total_reclaimed, 1)
        self.assertEqual(total_stale, 20)
        self.assertEqual(stats.heap_items, 0)

    def test_renew_expired_key_fails(self):
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        store.put("k", "v", ttl=1, size=1)
        clock.advance(5)
        with self.assertRaises(KeyExpiredError):
            store.renew("k", ttl=10)
        self.assertNotIn("k", store)

    def test_overwrite_old_heap_item_becomes_tombstone(self):
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        store.put("k", "old", ttl=1, size=5)
        store.put("k", "new", ttl=100, size=7)
        clock.advance(2)
        r = store.sweep_batch(100)
        self.assertEqual(r.stale_heap_items, 1)
        self.assertEqual(r.reclaimed, 0)
        self.assertEqual(store.get("k"), "new")
        self.assertEqual(store.stats().live_bytes, 7)


class ClockRollbackTest(unittest.TestCase):
    def test_rollback_before_collect_revives_visibility(self):
        """时钟回拨：未物理清理前，可见性完全由 expires_at vs now 决定，
        回拨会让“逻辑上已过期”的对象重新可见（注入时钟的自然语义）。"""
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        store.put("k", "v", ttl=10, size=1)
        clock.set(15)
        self.assertNotIn("k", store)
        clock.set(5)  # 回拨
        self.assertIn("k", store)
        self.assertEqual(store.get("k"), "v")
        self.assertEqual(store.renew("k", 100), 105)

    def test_rollback_after_collect_object_stays_deleted(self):
        """已物理删除的对象不会因时钟回拨复活。"""
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        store.put("k", "v", ttl=10, size=1)
        clock.set(15)
        store.sweep_batch(100)
        clock.set(0)  # 回拨到写入之前
        self.assertNotIn("k", store)
        self.assertEqual(store.stats().live_objects, 0)

    def test_rollback_with_live_and_expired_mix(self):
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        store.put("short", 1, ttl=2, size=1)
        store.put("long", 2, ttl=100, size=1)
        clock.set(10)
        store.sweep_batch(100)  # 回收 short
        self.assertEqual(store.stats().live_objects, 1)
        clock.set(1)  # 大幅回拨
        self.assertNotIn("short", store)  # 已物理删除，不复活
        self.assertIn("long", store)      # 仍在，且可见

    def test_monotonic_clock_is_immune_to_wall_rollback(self):
        """生产时钟基于 monotonic_ns，不受墙上时间回拨影响。"""
        from lifecycle.store import MonotonicClock
        c = MonotonicClock()
        t1, t2 = c(), c()
        self.assertLessEqual(t1, t2)


class ConcurrencySafetyTest(unittest.TestCase):
    """清理过程中不得删除被续期或正在读取的对象（断言测试）。"""

    def test_sweep_skips_object_being_renewed_at_expiry(self):
        """确定性交错：续期通过可见性检查后、提交新过期时刻前，对象到达
        过期点；此时清理批次必须跳过它，续期提交后对象存活。"""
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        store.put("k", "v", ttl=10, size=1)

        renewed = threading.Event()
        sweep_started = threading.Event()
        errors = []

        def renewer():
            try:
                def stall():
                    sweep_started.set()
                    clock.set(10)  # 临界窗口内对象恰好过期
                    renewed.wait(timeout=2)

                new_expiry = store.renew("k", ttl=100, _stall=stall)
                self.assertEqual(new_expiry, 110)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=renewer)
        t.start()
        self.assertTrue(sweep_started.wait(timeout=2))
        r = store.sweep_batch(100)
        # 关键断言：过期且 pinned -> 跳过而不是删除
        self.assertEqual(r.skipped_pinned, 1)
        self.assertEqual(r.reclaimed, 0)
        self.assertTrue(r.blocked_pinned)
        self.assertEqual(store.stats().live_objects, 1)
        renewed.set()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertEqual(errors, [])
        # 续期已生效：新过期时刻 110，在 t=10 仍然可见
        self.assertIn("k", store)
        self.assertEqual(store.get("k"), "v")
        # 引用结束后，旧版本堆项作为墓碑丢弃；对象按新过期时刻(110)存活
        r2 = store.sweep_batch(100)
        self.assertEqual(r2.reclaimed, 0)
        self.assertIn("k", store)
        stats = store.stats()
        self.assertEqual(stats.live_objects, 1)
        self.assertGreaterEqual(r2.stale_heap_items, 1)

    def test_sweep_skips_object_being_read(self):
        """读取窗口内对象过期：清理跳过；读取结束后才回收。"""
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        store.put("k", "payload", ttl=10, size=10)

        inside = threading.Event()
        can_exit = threading.Event()
        read_ok = []

        def reader():
            with store.begin_access("k") as access:
                read_ok.append(access.value)
                inside.set()
                can_exit.wait(timeout=2)

        t = threading.Thread(target=reader)
        t.start()
        self.assertTrue(inside.wait(timeout=2))
        clock.set(10)  # 读取期间过期
        r = store.sweep_batch(100)
        self.assertEqual(r.skipped_pinned, 1)
        self.assertEqual(r.reclaimed, 0)
        can_exit.set()
        t.join(2)
        self.assertEqual(read_ok, ["payload"])
        # 读取结束后，下一批回收
        r2 = store.sweep_batch(100)
        self.assertEqual(r2.reclaimed, 1)

    def test_on_reclaim_never_fires_for_pinned_key_stress(self):
        """多线程随机交错压力 + 回收回调硬断言：任何被物理删除的 key，
        删除瞬间 pins 计数必为 0。"""
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        n_keys = 500
        for i in range(n_keys):
            store.put(i, f"value-{i}", ttl=30, size=10)

        errors = []
        stop = threading.Event()
        barrier = threading.Barrier(7)

        def record_error(ctx):
            errors.append(ctx)

        def check_not_pinned(key):
            # 回调持有 store 锁；若 key 仍被 pin，内部状态已损坏。
            if key in store._pins:  # noqa: SLF001
                record_error(f"reclaimed pinned key: {key}")

        store.on_reclaim = check_not_pinned

        def clock_ticker():
            try:
                barrier.wait()
                while not stop.is_set():
                    clock.advance(1)
                    time.sleep(0.0005)
            except BaseException as exc:  # noqa: BLE001
                record_error(exc)

        def worker(worker_id):
            import random

            rng = random.Random(worker_id)
            try:
                barrier.wait()
                while not stop.is_set():
                    key = rng.randrange(n_keys)
                    action = rng.random()
                    try:
                        if action < 0.35:
                            store.renew(key, ttl=rng.randint(1, 40))
                        elif action < 0.7:
                            with store.begin_access(key) as access:
                                if access.value != f"value-{key}":
                                    record_error("value corruption")
                                time.sleep(rng.random() * 0.001)
                        else:
                            store.get(key)
                    except (KeyError, KeyExpiredError):
                        pass
            except BaseException as exc:  # noqa: BLE001
                record_error(exc)

        def sweeper():
            try:
                barrier.wait()
                while not stop.is_set():
                    store.sweep_batch(budget=64)
                    time.sleep(0.0002)
            except BaseException as exc:  # noqa: BLE001
                record_error(exc)

        threads = [threading.Thread(target=clock_ticker)]
        threads += [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        threads.append(threading.Thread(target=sweeper))
        for th in threads:
            th.start()
        time.sleep(1.5)
        stop.set()
        for th in threads:
            th.join(3)
            self.assertFalse(th.is_alive(), "worker thread hung")
        self.assertEqual(errors, [])
        # 收尾：所有对象最终要么存活可见，要么被安全回收
        clock.advance(10000)
        store.sweep_drain(1000)
        self.assertEqual(store.stats().live_objects, 0)
        self.assertEqual(store.stats().pinned_objects, 0)


class ScaleAndBackgroundTest(unittest.TestCase):
    def test_many_objects_expiring_simultaneously(self):
        """海量对象全部同时过期：分批回收，每批工作量严格 <= budget。"""
        n = 50_000
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        for i in range(n):
            store.put(i, i, ttl=10, size=1)
        self.assertEqual(store.stats().visible_objects, n)
        clock.advance(11)
        self.assertEqual(store.stats().visible_objects, 0)  # 立即不可见

        budget = 4096
        batches = 0
        inspected_total = 0
        max_inspected = 0
        while True:
            r = store.sweep_batch(budget)
            batches += 1
            inspected_total += r.inspected
            max_inspected = max(max_inspected, r.inspected)
            self.assertLessEqual(r.inspected, budget)  # 工作量上界
            if r.early_stop or r.inspected < budget:
                break
        self.assertEqual(store.stats().live_objects, 0)
        self.assertEqual(store.stats().total_reclaimed, n)
        self.assertLessEqual(max_inspected, budget)
        # 恰好整除时最后一批清空堆（总计 n）；否则最后多 1 次“看堆顶”
        self.assertIn(inspected_total, (n, n + 1))
        self.assertGreater(batches, 1)

    def test_interval_sweeper_background(self):
        from lifecycle.store import MonotonicClock

        clock = MonotonicClock()
        store = LifecycleStore(clock=clock)
        store.put("a", 1, ttl=20_000_000, size=1)  # 20ms
        sweeper = IntervalSweeper(store, interval=5_000_000, budget=100)  # 5ms
        sweeper.start()
        deadline = time.time() + 2
        while time.time() < deadline and store.stats().live_objects:
            time.sleep(0.01)
        sweeper.stop(1)
        self.assertEqual(store.stats().live_objects, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
