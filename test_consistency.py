"""Self-tests for session-level consistency routing (stdlib unittest)."""
import threading
import time
import unittest

from db_cluster import Primary, Replica
from router import ConsistentRouter, NaiveRouter


def make_cluster(lags, query_delay=0.0):
    primary = Primary(query_delay=query_delay)
    replicas = [Replica(primary, lag, name=f"r{i}", query_delay=query_delay)
                for i, lag in enumerate(lags)]
    return primary, replicas


def stop_all(replicas):
    for r in replicas:
        r.stop()


class TestReadYourWrites(unittest.TestCase):
    def test_zero_lag_replica_serves_own_write(self):
        primary, replicas = make_cluster([0.0])
        router = ConsistentRouter(primary, replicas,
                                  lag_threshold=0.05, catchup_timeout=0.5)
        s = router.session()
        router.write(s, "k", "v1")
        self.assertEqual(router.read(s, "k"), "v1")
        snap = router.stats.snapshot()
        self.assertEqual(snap["violations"], 0)
        self.assertEqual(snap["fallbacks"], 0)  # caught up within timeout
        stop_all(replicas)

    def test_lag_far_above_threshold_falls_back_to_primary(self):
        primary, replicas = make_cluster([5.0])  # 5s lag >> 50ms threshold
        router = ConsistentRouter(primary, replicas,
                                  lag_threshold=0.05, catchup_timeout=0.02)
        s = router.session()
        router.write(s, "k", "v1")
        self.assertEqual(router.read(s, "k"), "v1")  # correct despite lag
        snap = router.stats.snapshot()
        self.assertEqual(snap["violations"], 0)
        self.assertEqual(snap["fallbacks"], 1)
        self.assertEqual(snap["replica_reads"], 0)
        stop_all(replicas)

    def test_naive_router_violates_ryw_under_lag(self):
        """Baseline: without consistency control, violations must appear."""
        primary, replicas = make_cluster([0.3])
        router = NaiveRouter(primary, replicas)
        s = router.session()
        for i in range(20):
            router.write(s, "k", f"v{i}")
            router.read(s, "k")
        snap = router.stats.snapshot()
        self.assertGreater(snap["violations"], 0)
        stop_all(replicas)


class TestMonotonicReads(unittest.TestCase):
    def test_values_never_regress_across_replicas(self):
        # Two replicas with very different lags; session may bounce between
        # them and the primary, but must never see an older value.
        primary, replicas = make_cluster([0.02, 0.4])
        router = ConsistentRouter(primary, replicas,
                                  lag_threshold=1.0, catchup_timeout=0.0)
        s = router.session()
        seen = []
        for i in range(30):
            router.write(s, "k", i)
            seen.append(router.read(s, "k"))
            time.sleep(0.005)
        self.assertEqual(seen, sorted(seen))          # never regresses
        self.assertEqual(seen[-1], 29)
        self.assertEqual(router.stats.snapshot()["violations"], 0)
        stop_all(replicas)

    def test_naive_router_violates_monotonicity(self):
        primary, replicas = make_cluster([0.0, 0.3])
        router = NaiveRouter(primary, replicas)
        s = router.session()
        seen = []
        for i in range(40):
            router.write(s, "k", i)
            seen.append(router.read(s, "k"))
            time.sleep(0.002)
        self.assertGreater(router.stats.snapshot()["violations"], 0)
        norm = [-1 if v is None else v for v in seen]
        self.assertNotEqual(norm, sorted(norm))
        stop_all(replicas)


class TestFailover(unittest.TestCase):
    def test_single_replica_failure_still_serves(self):
        primary, replicas = make_cluster([0.01, 0.01])
        router = ConsistentRouter(primary, replicas,
                                  lag_threshold=0.1, catchup_timeout=0.3)
        s = router.session()
        router.write(s, "k", "v1")
        self.assertEqual(router.read(s, "k"), "v1")
        replicas[0].kill()
        router.write(s, "k", "v2")
        self.assertEqual(router.read(s, "k"), "v2")  # served by r1 or primary
        self.assertEqual(router.stats.snapshot()["violations"], 0)
        stop_all(replicas)

    def test_all_replicas_down_reads_go_to_primary(self):
        primary, replicas = make_cluster([0.01, 0.01])
        router = ConsistentRouter(primary, replicas,
                                  lag_threshold=0.1, catchup_timeout=0.05)
        s = router.session()
        router.write(s, "k", "v1")
        for r in replicas:
            r.kill()
        for i in range(5):
            router.write(s, "k", f"v{i+2}")
            self.assertEqual(router.read(s, "k"), f"v{i+2}")
        snap = router.stats.snapshot()
        self.assertEqual(snap["violations"], 0)
        self.assertEqual(snap["replica_reads"], 0)
        self.assertEqual(snap["fallbacks"], 5)  # every read hit the primary
        stop_all(replicas)

    def test_replica_killed_mid_read_falls_back(self):
        primary, replicas = make_cluster([0.0])
        router = ConsistentRouter(primary, replicas,
                                  lag_threshold=0.1, catchup_timeout=0.3)
        s = router.session()
        router.write(s, "k", "v1")
        replicas[0].kill()  # kill between pick and read is exercised via race
        for _ in range(10):
            self.assertEqual(router.read(s, "k"), "v1")
        self.assertEqual(router.stats.snapshot()["violations"], 0)
        stop_all(replicas)


class TestConcurrentReads(unittest.TestCase):
    def test_concurrent_sessions_zero_violations(self):
        primary, replicas = make_cluster([0.05, 0.15])
        router = ConsistentRouter(primary, replicas,
                                  lag_threshold=0.05, catchup_timeout=0.05)
        errors = []

        def worker(tid):
            try:
                s = router.session()
                key = f"user:{tid}"
                last = -1
                for i in range(30):
                    router.write(s, key, i)
                    got = router.read(s, key)
                    if got < last or got != i:   # RYW + monotonic per key
                        errors.append((tid, i, got, last))
                    last = got
            except Exception as exc:             # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,))
                   for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        snap = router.stats.snapshot()
        self.assertEqual(snap["violations"], 0)
        self.assertEqual(snap["total_reads"], 8 * 30)
        stop_all(replicas)


if __name__ == "__main__":
    unittest.main(verbosity=2)
