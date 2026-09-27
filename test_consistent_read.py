"""Consistency test-suite for consistent_read.py.

Run:  python3 -m unittest -v
"""

import threading
import time
import unittest

from consistent_read import Metrics, Primary, Replica, Router


def make_stack(lag=0.0, threshold=0.05, guarded=True, replicas=1):
    primary = Primary()
    reps = [Replica(primary, lag=lag) for _ in range(replicas)]
    metrics = Metrics()
    router = Router(primary, reps, lag_threshold=threshold, guarded=guarded, metrics=metrics)
    return primary, reps, router, metrics


def wait_until(pred, timeout=3.0, interval=0.002):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


class TestReadYourWrites(unittest.TestCase):
    def test_zero_lag_replica_serves_own_write(self):
        """Lag ~ 0: replica catches up almost immediately, reads hit the replica."""
        primary, reps, router, metrics = make_stack(lag=0.0, threshold=0.05)
        s = router.session()
        s.write("k", "v1")
        self.assertTrue(wait_until(lambda: reps[0].applied_lsn >= 1))
        for _ in range(20):
            self.assertEqual(s.read("k"), "v1")
        snap = metrics.snapshot()
        self.assertEqual(snap["violations"], 0)
        self.assertGreater(snap["replica_reads"], 0)  # actually served by replica

    def test_huge_lag_falls_back_and_still_sees_own_write(self):
        """Lag (10s) >> threshold (50ms): every read falls back, value is correct."""
        primary, reps, router, metrics = make_stack(lag=10.0, threshold=0.05)
        s = router.session()
        for i in range(10):
            s.write("k", i)
            self.assertEqual(s.read("k"), i)  # RYW must hold despite the lag
        snap = metrics.snapshot()
        self.assertEqual(snap["violations"], 0)
        self.assertEqual(snap["fallback_ratio"], 1.0)  # 100% fallback

    def test_naive_router_violates_guarded_does_not(self):
        """Same workload, naive vs guarded: violations > 0 vs violations == 0."""
        # Naive: always read the (lagging) replica.
        _, _, naive_router, naive_metrics = make_stack(lag=0.3, guarded=False)
        s = naive_router.session()
        for i in range(30):
            s.write("k", i)
            s.read("k")
        self.assertGreater(naive_metrics.snapshot()["violations"], 0)

        # Guarded: identical workload, zero violations.
        _, _, router, metrics = make_stack(lag=0.3, threshold=0.05, guarded=True)
        s = router.session()
        for i in range(30):
            s.write("k", i)
            self.assertEqual(s.read("k"), i)
        snap = metrics.snapshot()
        self.assertEqual(snap["violations"], 0)
        self.assertGreater(snap["fallback_reads"], 0)


class TestMonotonicReads(unittest.TestCase):
    def test_never_reads_older_state(self):
        """After observing LSN N, a later read must not come back with LSN < N."""
        primary, reps, router, metrics = make_stack(lag=10.0, threshold=0.05)
        s = router.session()
        s.write("k", "v1")
        self.assertEqual(s.read("k"), "v1")   # fallback, observes LSN 1
        s.write("k", "v2")
        # Replica is still at LSN 0; an unguarded read would return None (older).
        for _ in range(10):
            self.assertEqual(s.read("k"), "v2")
        self.assertEqual(metrics.snapshot()["violations"], 0)

    def test_monotonic_across_external_writes(self):
        """Writes by *other* sessions still cannot move a session backwards."""
        primary, reps, router, metrics = make_stack(lag=0.0, threshold=0.05)
        s1, s2 = router.session(), router.session()
        s1.write("k", "a")
        self.assertTrue(wait_until(lambda: reps[0].applied_lsn >= 1))
        self.assertEqual(s2.read("k"), "a")          # s2 observes LSN 1 on replica
        s1.write("k", "b")                            # LSN 2, not yet replicated
        # Replica may briefly be at LSN 1 < s2.last_read_lsn -> must fall back.
        for _ in range(20):
            self.assertIn(s2.read("k"), ("a", "b"))   # never None / never older LSN
        self.assertEqual(metrics.snapshot()["violations"], 0)


class TestLagThresholdPolicy(unittest.TestCase):
    def test_lag_within_threshold_uses_replica(self):
        """Lag 10ms < threshold 200ms: after catch-up, reads use the replica."""
        primary, reps, router, metrics = make_stack(lag=0.01, threshold=0.2)
        s = router.session()
        s.write("k", "v")
        self.assertTrue(wait_until(lambda: reps[0].applied_lsn >= 1))
        time.sleep(0.05)  # fully caught up -> estimated lag back to 0
        for _ in range(10):
            self.assertEqual(s.read("k"), "v")
        snap = metrics.snapshot()
        self.assertEqual(snap["violations"], 0)
        self.assertEqual(snap["replica_reads"], 10)

    def test_lag_above_threshold_falls_back(self):
        """Lag 300ms > threshold 50ms: while behind, reads fall back."""
        primary, reps, router, metrics = make_stack(lag=0.3, threshold=0.05)
        s = router.session()
        s.write("k", "v")
        time.sleep(0.1)  # oldest unapplied entry is ~100ms old > 50ms threshold
        for _ in range(10):
            s.read("k")
        snap = metrics.snapshot()
        self.assertEqual(snap["violations"], 0)
        self.assertEqual(snap["fallback_reads"], 10)
        # After the 300ms lag elapses the replica applies the entry and serves again.
        self.assertTrue(wait_until(lambda: reps[0].applied_lsn >= 1))
        self.assertEqual(s.read("k"), "v")


class TestFailover(unittest.TestCase):
    def test_replica_down_falls_back_then_recovers(self):
        primary, reps, router, metrics = make_stack(lag=0.0, threshold=0.05)
        s = router.session()
        s.write("k", "v1")
        self.assertTrue(wait_until(lambda: reps[0].applied_lsn >= 1))
        self.assertEqual(s.read("k"), "v1")
        self.assertEqual(metrics.snapshot()["replica_reads"], 1)

        reps[0].set_down(True)  # simulate replica failure
        for _ in range(5):
            self.assertEqual(s.read("k"), "v1")  # served by primary, no exception
        mid = metrics.snapshot()
        self.assertEqual(mid["fallback_reads"], 5)
        self.assertEqual(mid["violations"], 0)

        reps[0].set_down(False)  # replica recovers and catches up
        self.assertTrue(wait_until(lambda: reps[0].applied_lsn >= 1))
        self.assertEqual(s.read("k"), "v1")
        snap = metrics.snapshot()
        self.assertEqual(snap["replica_reads"], 2)
        self.assertEqual(snap["violations"], 0)

    def test_all_replicas_down(self):
        primary, reps, router, metrics = make_stack(lag=0.0, replicas=2)
        s = router.session()
        s.write("k", "v")
        for r in reps:
            r.set_down(True)
        for _ in range(5):
            self.assertEqual(s.read("k"), "v")
        snap = metrics.snapshot()
        self.assertEqual(snap["fallback_ratio"], 1.0)
        self.assertEqual(snap["violations"], 0)


class TestConcurrentReads(unittest.TestCase):
    def test_concurrent_sessions_zero_violations(self):
        """Many threads doing write->read cycles against a lagging replica."""
        primary, reps, router, metrics = make_stack(lag=0.05, threshold=0.02, replicas=2)
        errors = []
        threads = []
        barrier = threading.Barrier(8)

        # A shared key, fully replicated before the storm starts.
        seed = router.session()
        seed.write("shared", "v")
        self.assertTrue(wait_until(lambda: all(r.applied_lsn >= 1 for r in reps)))

        def writer(tid):
            try:
                s = router.session()
                key = f"key-{tid}"
                barrier.wait()
                for i in range(50):
                    s.write(key, i)
                    got = s.read(key)
                    if got != i:  # RYW under concurrency
                        errors.append((tid, i, got))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def reader():
            try:
                s = router.session()
                barrier.wait()
                for _ in range(50):
                    if s.read("shared") != "v":
                        errors.append("stale shared read")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        for tid in range(4):
            threads.append(threading.Thread(target=writer, args=(tid,)))
        for _ in range(4):
            threads.append(threading.Thread(target=reader))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        snap = metrics.snapshot()
        self.assertEqual(snap["total_reads"], 8 * 50)
        self.assertEqual(snap["violations"], 0)
        self.assertGreater(snap["replica_reads"], 0)   # mix of both paths
        self.assertGreater(snap["fallback_reads"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
