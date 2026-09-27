"""Constructed-scenario tests for deadlock_detector.

Run:  python3 -m unittest test_deadlock_detector -v
"""

import threading
import time
import unittest

import deadlock_detector as dd


def wait_until(predicate, timeout=5.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def make_deadlock(locks, barrier):
    """Spawn one thread per lock: thread i holds locks[i], waits locks[(i+1)%n]."""
    n = len(locks)
    for i in range(n):
        first, second = locks[i], locks[(i + 1) % n]

        def worker(first=first, second=second, idx=i):
            first.acquire()
            barrier.wait()
            second.acquire()  # blocks forever -> daemon thread

        threading.Thread(target=worker, name=f"T{i}-{'-'.join(l.name for l in locks)}",
                         daemon=True).start()


class DeadlockDetectionTests(unittest.TestCase):
    def setUp(self):
        dd.reset()

    def tearDown(self):
        dd.reset()

    def test_two_thread_deadlock(self):
        la, lb = dd.TrackedLock("A"), dd.TrackedLock("B")
        make_deadlock([la, lb], threading.Barrier(2))
        self.assertTrue(wait_until(lambda: dd.waiter_count() >= 2))

        cycles = dd.detect()
        self.assertEqual(len(cycles), 1)
        cycle = cycles[0]
        self.assertEqual(len(cycle.steps), 2)
        self.assertEqual({s.thread_name for s in cycle.steps},
                         {s.thread_name for s in cycle.steps})  # sanity
        self.assertEqual(len({s.thread_ident for s in cycle.steps}), 2)
        text = cycle.format()
        self.assertIn("holds <A>", text)
        self.assertIn("holds <B>", text)
        self.assertIn("waits for <A>", text)
        self.assertIn("waits for <B>", text)
        self.assertIn("blocked", text)
        for step in cycle.steps:
            self.assertGreaterEqual(step.blocked_seconds, 0)

    def test_three_thread_deadlock(self):
        locks = [dd.TrackedLock(n) for n in ("A", "B", "C")]
        make_deadlock(locks, threading.Barrier(3))
        self.assertTrue(wait_until(lambda: dd.waiter_count() >= 3))

        cycles = dd.detect()
        self.assertEqual(len(cycles), 1)
        self.assertEqual(len(cycles[0].steps), 3)
        text = dd.report()
        self.assertIn("3 threads", text)
        for name in ("A", "B", "C"):
            self.assertIn(f"waits for <{name}>", text)

    def test_multiple_cycles_reported_and_sorted(self):
        # Build a 3-thread cycle first, then (later) a 2-thread cycle, so the
        # 3-thread cycle has the longer blocked duration and must sort first.
        locks3 = [dd.TrackedLock(n) for n in ("A", "B", "C")]
        make_deadlock(locks3, threading.Barrier(3))
        self.assertTrue(wait_until(lambda: dd.waiter_count() >= 3))
        time.sleep(0.2)

        locks2 = [dd.TrackedLock(n) for n in ("X", "Y")]
        make_deadlock(locks2, threading.Barrier(2))
        self.assertTrue(wait_until(lambda: dd.waiter_count() >= 5))

        cycles = dd.detect()
        self.assertEqual(len(cycles), 2)
        self.assertEqual([len(c.steps) for c in cycles], [3, 2])
        self.assertGreaterEqual(cycles[0].max_blocked_seconds,
                                cycles[1].max_blocked_seconds)
        self.assertIn("2 deadlock cycle(s)", dd.report())

    def test_one_way_wait_is_not_deadlock(self):
        lock = dd.TrackedLock("L")
        acquired = threading.Event()
        release_me = threading.Event()

        def holder():
            lock.acquire()
            release_me.wait(5)
            lock.release()

        def waiter():
            lock.acquire()
            acquired.set()
            lock.release()

        t1 = threading.Thread(target=holder, name="holder", daemon=True)
        t2 = threading.Thread(target=waiter, name="waiter", daemon=True)
        t1.start()
        self.assertTrue(wait_until(lambda: lock.locked()))
        t2.start()
        self.assertTrue(wait_until(lambda: dd.waiter_count() >= 1))

        self.assertEqual(dd.detect(), [])
        self.assertEqual(dd.report(), "no deadlocks detected")

        release_me.set()
        self.assertTrue(acquired.wait(5))  # waiter eventually proceeds
        t1.join(5)
        t2.join(5)

    def test_condition_wait_is_not_false_positive(self):
        cond = dd.TrackedCondition(name="cond")
        proceeded = threading.Event()

        def waiter():
            with cond:
                cond.wait(5)  # releases the lock while waiting
                proceeded.set()

        def notifier():
            self.assertTrue(wait_until(lambda: dd.waiter_count() >= 1))
            # While the waiter sleeps on the condition, there must be no cycle.
            self.assertEqual(dd.detect(), [])
            with cond:
                cond.notify_all()

        t1 = threading.Thread(target=waiter, name="cond-waiter", daemon=True)
        t2 = threading.Thread(target=notifier, name="notifier", daemon=True)
        t1.start()
        t2.start()
        self.assertTrue(proceeded.wait(5))
        t1.join(5)
        t2.join(5)
        self.assertEqual(dd.detect(), [])

    def test_timed_lock_is_not_false_positive(self):
        lock = dd.TrackedLock("L")
        done = threading.Event()

        def holder():
            lock.acquire()
            time.sleep(0.5)
            lock.release()

        def timed_waiter():
            while not lock.acquire(timeout=0.1):  # timed waits: excluded
                if done.is_set():
                    return
            lock.release()
            done.set()

        t1 = threading.Thread(target=holder, name="holder", daemon=True)
        t2 = threading.Thread(target=timed_waiter, name="timed", daemon=True)
        t1.start()
        t2.start()
        self.assertTrue(wait_until(lambda: dd.waiter_count() >= 1))
        time.sleep(0.2)  # t2 is inside a timed wait now
        self.assertEqual(dd.detect(), [])
        self.assertTrue(done.wait(5))
        t1.join(5)
        t2.join(5)

    def test_rlock_reentrant_acquire_is_not_self_deadlock(self):
        rlock = dd.TrackedRLock("R")
        rlock.acquire()
        rlock.acquire()  # reentrant: no wait edge, no cycle
        rlock.acquire()
        self.assertEqual(dd.detect(), [])
        rlock.release()
        rlock.release()
        rlock.release()

    def test_plain_lock_self_deadlock_is_detected(self):
        lock = dd.TrackedLock("S")

        def self_blocker():
            lock.acquire()
            lock.acquire()  # non-reentrant: blocks on itself forever

        threading.Thread(target=self_blocker, name="self-blocker", daemon=True).start()
        self.assertTrue(wait_until(lambda: dd.waiter_count() >= 1))

        cycles = dd.detect()
        self.assertEqual(len(cycles), 1)
        self.assertEqual(len(cycles[0].steps), 1)
        step = cycles[0].steps[0]
        self.assertEqual(step.thread_name, "self-blocker")
        self.assertEqual(step.holds_lock, "S")
        self.assertEqual(step.waits_for_lock, "S")


if __name__ == "__main__":
    unittest.main(verbosity=2)
