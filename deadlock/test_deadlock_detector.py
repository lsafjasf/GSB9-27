"""
构造场景自测: 验证死锁检测的准确性与误判排除规则.

运行: python3 -m unittest test_deadlock_detector -v
   或: python3 test_deadlock_detector.py
"""

import threading
import time
import unittest

from deadlock_detector import (
    TrackedLock,
    TrackedRLock,
    TrackedCondition,
    detector,
)


def quiet_release(lock):
    """释放锁; 主线程为解开死锁可能已代为释放过, 忽略 RuntimeError."""
    try:
        lock.release()
    except RuntimeError:
        pass


def wait_until(predicate, timeout=5.0, interval=0.01):
    """轮询等待条件成立, 超时返回 False."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class DeadlockDetectionTest(unittest.TestCase):

    def setUp(self):
        detector._reset()

    def tearDown(self):
        detector._reset()

    # ---------- 场景 1: 两线程死锁 -> 必须检出 ----------

    def test_two_thread_deadlock_detected(self):
        lock_a = TrackedLock("A")
        lock_b = TrackedLock("B")
        a_held, b_held = threading.Event(), threading.Event()
        errors = []

        def t1():
            try:
                lock_a.acquire()
                a_held.set()
                b_held.wait(5)
                lock_b.acquire()   # 等 T2 持有的 B
                lock_b.release()
                quiet_release(lock_a)
            except Exception as exc:  # 工作线程异常必须冒泡为测试失败
                errors.append(("t1", repr(exc)))

        def t2():
            try:
                lock_b.acquire()
                b_held.set()
                a_held.wait(5)
                lock_a.acquire()   # 等 T1 持有的 A
                lock_a.release()
                quiet_release(lock_b)
            except Exception as exc:
                errors.append(("t2", repr(exc)))

        th1 = threading.Thread(target=t1, name="worker-1")
        th2 = threading.Thread(target=t2, name="worker-2")
        th1.start()
        th2.start()
        try:
            # 等双方都进入等待状态
            self.assertTrue(wait_until(lambda: len(detector._snapshot()[0]) == 2))
            time.sleep(0.2)  # 让阻塞时长可观测
            reports = detector.detect()
            self.assertEqual(len(reports), 1, "应检出恰好一个死锁环")
            report = reports[0]
            self.assertEqual(sorted(report.thread_names), ["worker-1", "worker-2"])
            self.assertGreater(report.blocked_seconds, 0.1)
            # 环路径: 两条边, 每条都是 "谁 --等哪把锁--> 持有者"
            chain = {(w, l, h) for w, l, h, _ in report.waits}
            self.assertEqual(chain, {
                ("worker-1", "B", "worker-2"),
                ("worker-2", "A", "worker-1"),
            })
            # 每个持有者的持锁时长都应被报告
            self.assertEqual(len(report.holds), 2)
            for _, _, held in report.holds:
                self.assertGreater(held, 0.1)
            print("\n" + report.format())
        finally:
            # Lock 允许跨线程释放, 解开死锁以便清理
            lock_a.release()
            lock_b.release()
            th1.join(5)
            th2.join(5)
        self.assertFalse(th1.is_alive() or th2.is_alive())
        self.assertEqual(errors, [], "两个工作线程都不应抛出异常")

    # ---------- 场景 2: 三线程死锁 -> 必须检出完整三环 ----------

    def test_three_thread_deadlock_detected(self):
        locks = [TrackedLock("L%d" % i) for i in range(3)]
        held = [threading.Event() for _ in range(3)]

        def worker(i):
            locks[i].acquire()
            held[i].set()
            for e in held:
                e.wait(5)
            locks[(i + 1) % 3].acquire()   # 等下一位的锁 -> 三元环
            locks[(i + 1) % 3].release()
            quiet_release(locks[i])

        threads = [threading.Thread(target=worker, args=(i,),
                                    name="ring-%d" % i) for i in range(3)]
        for t in threads:
            t.start()
        try:
            self.assertTrue(wait_until(lambda: len(detector._snapshot()[0]) == 3))
            time.sleep(0.2)
            reports = detector.detect()
            self.assertEqual(len(reports), 1)
            report = reports[0]
            self.assertEqual(sorted(report.thread_names),
                             ["ring-0", "ring-1", "ring-2"])
            # 环路径必须是 ring-0 -> ring-1 -> ring-2 -> ring-0
            chain = [(w, h) for w, _, h, _ in report.waits]
            self.assertEqual(len(chain), 3)
            successors = dict(chain)
            node = "ring-0"
            visited = []
            for _ in range(3):
                visited.append(node)
                node = successors[node]
            self.assertEqual(node, "ring-0")
            self.assertEqual(sorted(visited), ["ring-0", "ring-1", "ring-2"])
            print("\n" + report.format())
        finally:
            for lk in locks:
                lk.release()
            for t in threads:
                t.join(5)
        self.assertFalse(any(t.is_alive() for t in threads))

    # ---------- 场景 3: 单向等待 -> 不得报死锁 ----------

    def test_one_way_wait_is_not_deadlock(self):
        lock = TrackedLock("one-way")
        entered = threading.Event()
        release_now = threading.Event()

        def holder():
            lock.acquire()
            entered.set()
            release_now.wait(5)
            lock.release()

        def waiter():
            entered.wait(5)
            lock.acquire()   # 单纯排队等 holder 释放, 不构成环
            lock.release()

        th1 = threading.Thread(target=holder, name="holder")
        th2 = threading.Thread(target=waiter, name="waiter")
        th1.start()
        th2.start()
        self.assertTrue(wait_until(lambda: len(detector._snapshot()[0]) == 1))
        time.sleep(0.2)
        reports = detector.detect()
        self.assertEqual(reports, [], "单向等待不是死锁, 不应有任何报告")
        release_now.set()
        th1.join(5)
        th2.join(5)
        self.assertFalse(th1.is_alive() or th2.is_alive())

    # ---------- 场景 4: 条件变量等待 -> 不得误判 ----------

    def test_condition_wait_is_not_deadlock(self):
        cond = TrackedCondition(name="cond")
        waiting = threading.Event()
        proceed = threading.Event()
        state = {"done": False}

        def consumer():
            with cond:
                waiting.set()
                # wait 期间释放底层锁; 被唤醒后重新获取锁会阻塞在 producer 手上,
                # 这属于条件变量语义, 不得登记为等待边.
                cond.wait_for(lambda: state["done"], timeout=5)

        def producer():
            waiting.wait(5)
            with cond:
                proceed.wait(5)   # 持锁停留一段时间, 制造"唤醒后重取锁被阻塞"窗口
                state["done"] = True
                cond.notify_all()
                time.sleep(0.3)   # notify 后继续持锁, consumer 重取锁必然阻塞

        th1 = threading.Thread(target=consumer, name="consumer")
        th2 = threading.Thread(target=producer, name="producer")
        th1.start()
        th2.start()
        # 窗口 1: consumer 在 cond.wait 中, producer 尚未拿锁
        def consumer_in_cond_wait():
            with detector._mu:
                return th1.ident in detector._cond_waiters

        self.assertTrue(wait_until(consumer_in_cond_wait),
                        "consumer 必须确实进入 Condition.wait 状态")
        time.sleep(0.2)
        self.assertEqual(detector.detect(), [],
                         "条件变量等待期间不应报死锁")
        # 窗口 2: producer 持锁且已 notify, consumer 正在重取锁
        proceed.set()
        time.sleep(0.2)
        self.assertEqual(detector.detect(), [],
                         "条件变量唤醒后的锁重取不应报死锁")
        th1.join(5)
        th2.join(5)
        self.assertFalse(th1.is_alive() or th2.is_alive())
        self.assertTrue(state["done"])

    # ---------- 场景 5: 超时锁 -> 成环边含超时, 不得误判 ----------

    def test_timeout_lock_breaks_cycle(self):
        lock_a = TrackedLock("TA")
        lock_b = TrackedLock("TB")
        a_held, b_held = threading.Event(), threading.Event()
        results = {}

        def t1():
            lock_a.acquire()
            a_held.set()
            b_held.wait(5)
            # 带超时等待 B: 这条边会自行断开, 环不成立
            results["t1"] = lock_b.acquire(timeout=0.4)
            lock_a.release()

        def t2():
            lock_b.acquire()
            b_held.set()
            a_held.wait(5)
            results["t2"] = lock_a.acquire()   # 无限期等 A, 但 A 很快会被 t1 释放
            lock_a.release()
            lock_b.release()

        th1 = threading.Thread(target=t1, name="timeout-waiter")
        th2 = threading.Thread(target=t2, name="blocking-waiter")
        th1.start()
        th2.start()
        # 两条等待边都已登记 (一超时一阻塞) 的时刻
        self.assertTrue(wait_until(lambda: len(detector._snapshot()[0]) >= 1
                                   and b_held.is_set() and a_held.is_set()))
        time.sleep(0.15)  # 超时尚未到 (0.4s), 图上存在"半个环"
        reports = detector.detect()
        self.assertEqual(reports, [],
                         "环上存在超时等待边, 不构成死锁, 不应报告")
        th1.join(5)
        th2.join(5)
        self.assertFalse(th1.is_alive() or th2.is_alive())
        self.assertIs(results["t1"], False, "t1 应超时获取失败")
        self.assertIs(results["t2"], True, "t2 应在 t1 释放后获得锁")

    # ---------- 场景 6: 可重入锁 -> 自等待不得成环 ----------

    def test_reentrant_lock_no_self_cycle(self):
        rlock = TrackedRLock("R")
        rlock.acquire()
        rlock.acquire()
        rlock.acquire()
        time.sleep(0.1)
        self.assertEqual(detector.detect(), [],
                         "同线程重入不应产生任何等待边或自环")
        rlock.release()
        rlock.release()
        rlock.release()

    # ---------- 场景 8: 普通锁同线程二次获取 -> 自锁, 必须检出自环 ----------

    def test_plain_lock_self_deadlock_detected(self):
        lock = TrackedLock("self-lock")
        acquired_once = threading.Event()

        def worker():
            lock.acquire()                  # 首次获取成功
            acquired_once.set()
            lock.acquire()                  # Lock 不可重入: 等待自己 -> 自锁
            lock.release()

        th = threading.Thread(target=worker, name="self-locker")
        th.start()
        try:
            self.assertTrue(acquired_once.wait(5))
            # 自环等待边 (waiter == holder) 必须被登记
            self.assertTrue(wait_until(lambda: len(detector._snapshot()[0]) == 1))
            time.sleep(0.2)  # 让阻塞时长可观测
            reports = detector.detect()
            self.assertEqual(len(reports), 1,
                             "同线程二次获取不可重入 Lock 应检出自锁")
            report = reports[0]
            self.assertEqual(report.thread_names, ["self-locker"])
            waiter_name, lock_name, holder_name, waited = report.waits[0]
            self.assertEqual(waiter_name, "self-locker")
            self.assertEqual(holder_name, "self-locker",
                             "自锁环的等待者与持有者应为同一线程")
            self.assertEqual(lock_name, "self-lock")
            self.assertGreater(waited, 0.1)
            self.assertEqual(len(report.holds), 1)
            print("\n" + report.format())
        finally:
            # worker 永远等不到自己释放, 由主线程代为释放以解开自锁
            lock.release()
            th.join(5)
        self.assertFalse(th.is_alive())

    # ---------- 场景 7: 多个独立等待环 -> 分别报告并按阻塞时长排序 ----------

    def test_multiple_cycles_reported_and_sorted(self):
        pair1 = (TrackedLock("P1-a"), TrackedLock("P1-b"))
        pair2 = (TrackedLock("P2-a"), TrackedLock("P2-b"))
        start1, start2 = threading.Event(), threading.Event()

        def deadlocked_pair(pair, start_evt, tag):
            def first():
                pair[0].acquire()
                start_evt.wait(5)
                time.sleep(0.05)
                pair[1].acquire()
                pair[1].release()
                quiet_release(pair[0])

            def second():
                pair[1].acquire()
                start_evt.wait(5)
                time.sleep(0.05)
                pair[0].acquire()
                pair[0].release()
                quiet_release(pair[1])

            t_a = threading.Thread(target=first, name="%s-x" % tag)
            t_b = threading.Thread(target=second, name="%s-y" % tag)
            t_a.start()
            t_b.start()
            return t_a, t_b

        g1 = deadlocked_pair(pair1, start1, "grp1")
        start1.set()
        # 让第一组先阻塞约 0.5s, 使两组阻塞时长明显不同
        self.assertTrue(wait_until(lambda: len(detector._snapshot()[0]) >= 2))
        time.sleep(0.5)
        g2 = deadlocked_pair(pair2, start2, "grp2")
        start2.set()
        try:
            self.assertTrue(wait_until(lambda: len(detector._snapshot()[0]) >= 4))
            time.sleep(0.2)
            reports = detector.detect()
            self.assertEqual(len(reports), 2, "两个独立死锁环应分别报告")
            # 按阻塞时长降序: 先进入死锁的 grp1 排前面
            self.assertGreaterEqual(reports[0].blocked_seconds,
                                    reports[1].blocked_seconds)
            self.assertIn("grp1-x", reports[0].thread_names)
            self.assertIn("grp2-x", reports[1].thread_names)
            # 两组的环互不串线
            self.assertEqual(
                {tuple(sorted(r.thread_names)) for r in reports},
                {("grp1-x", "grp1-y"), ("grp2-x", "grp2-y")})
            for r in reports:
                print("\n" + r.format())
        finally:
            for lk in pair1 + pair2:
                lk.release()
            for t in g1 + g2:
                t.join(5)
        self.assertFalse(any(t.is_alive() for t in g1 + g2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
