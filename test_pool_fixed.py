"""Regression tests for the fixed pool. Standard library only.

Run:  python3 -m unittest test_pool_fixed -v
"""

import threading
import time
import unittest

from pool_fixed import ConnectionPool, ConnectionClosedError, PoolError


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        self.closed = True

    def peer_close(self):
        """Simulate the remote end closing the connection (FIN/RST)."""
        self.closed = True

    def execute(self, sql="SELECT 1"):
        if self.closed:
            raise ConnectionClosedError("connection is closed")
        return "ok"


def make_pool(idle_timeout=30.0, max_idle=10, factory=None):
    clock = FakeClock()
    created = []

    def default_factory():
        conn = FakeConnection()
        created.append(conn)
        return conn

    pool = ConnectionPool(factory or default_factory,
                          idle_timeout=idle_timeout, max_idle=max_idle,
                          clock=clock)
    return pool, clock, created


class MisKillRegressionTest(unittest.TestCase):
    """The three reproduced mis-kill scenarios must not happen any more."""

    def test_borrowed_connection_is_never_reaped(self):
        pool, clock, _ = make_pool()
        conn = pool.borrow()
        clock.advance(3600.0)            # borrowed far beyond the timeout
        self.assertEqual(pool.reap_once(), 0)
        self.assertFalse(conn.closed)
        self.assertEqual(conn.execute(), "ok")   # still fully usable
        self.assertEqual(pool.stats()["borrowed"], 1)

    def test_reap_then_give_back_is_safe(self):
        """give_back landing right around a reap must not kill the conn."""
        pool, clock, _ = make_pool()
        conn = pool.borrow()
        clock.advance(3600.0)
        pool.reap_once()                 # scans while conn is borrowed
        pool.give_back(conn)             # returned right after the scan
        pool.reap_once()                 # fresh idle time -> must survive
        self.assertFalse(conn.closed)
        self.assertEqual(pool.stats()["idle"], 1)

    def test_just_borrowed_during_scan_survives(self):
        """An idle-expired conn borrowed just before the reap is safe."""
        pool, clock, _ = make_pool()
        conn = pool.borrow()
        pool.give_back(conn)
        clock.advance(31.0)              # genuinely idle-expired now
        borrowed = pool.borrow()         # borrower wins the race...
        self.assertIs(borrowed, conn)
        self.assertEqual(pool.reap_once(), 0)   # ...so nothing is reaped
        self.assertFalse(borrowed.closed)
        self.assertEqual(borrowed.execute(), "ok")

    def test_only_truly_idle_and_expired_connections_are_reaped(self):
        pool, clock, _ = make_pool()
        old, fresh, in_use = (pool.borrow() for _ in range(3))
        pool.give_back(old)              # old: idle since t=0
        clock.advance(31.0)
        pool.give_back(fresh)            # fresh: idle since t=31
        # in_use: borrowed at t=0, never returned
        clock.advance(20.0)              # t=51: old idle 51s, fresh 20s
        self.assertEqual(pool.reap_once(), 1)
        self.assertTrue(old.closed)      # expired idle -> reaped
        self.assertFalse(fresh.closed)   # idle but not expired -> kept
        self.assertFalse(in_use.closed)  # borrowed -> untouchable
        stats = pool.stats()
        self.assertEqual(stats["idle"], 1)
        self.assertEqual(stats["borrowed"], 1)
        self.assertEqual(stats["closed"], 1)


class IdleLimitTest(unittest.TestCase):
    def test_idle_count_over_max_closes_excess(self):
        pool, _, created = make_pool(max_idle=2)
        conns = [pool.borrow() for _ in range(3)]
        for conn in conns:
            pool.give_back(conn)
        stats = pool.stats()
        self.assertEqual(stats["idle"], 2)
        self.assertEqual(stats["closed"], 1)
        closed = [c for c in conns if c.closed]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].close_calls, 1)

    def test_returned_dead_connection_is_not_pooled(self):
        pool, _, _ = make_pool()
        conn = pool.borrow()
        conn.peer_close()
        pool.give_back(conn)
        stats = pool.stats()
        self.assertEqual(stats["idle"], 0)
        self.assertEqual(stats["closed"], 1)


class PeerClosedTest(unittest.TestCase):
    def test_borrow_skips_peer_closed_idle_connection(self):
        pool, _, created = make_pool()
        conn = pool.borrow()
        pool.give_back(conn)
        conn.peer_close()                # remote end drops it while idle
        recycled = pool.borrow()
        self.assertIsNot(recycled, conn)         # dead one was discarded
        self.assertFalse(recycled.closed)
        stats = pool.stats()
        self.assertEqual(stats["idle"], 0)
        self.assertEqual(stats["borrowed"], 1)
        self.assertEqual(stats["closed"], 1)
        self.assertEqual(len(created), 2)

    def test_reaper_drops_peer_closed_idle_connection(self):
        pool, clock, _ = make_pool()
        conn = pool.borrow()
        pool.give_back(conn)
        conn.peer_close()
        self.assertEqual(pool.reap_once(), 1)    # reaped even w/o expiry
        stats = pool.stats()
        self.assertEqual(stats["idle"], 0)
        self.assertEqual(stats["closed"], 1)


class ReaperReentrancyTest(unittest.TestCase):
    def test_concurrent_reapers_close_each_victim_exactly_once(self):
        pool, clock, _ = make_pool()
        victims = [pool.borrow() for _ in range(5)]
        for conn in victims:
            pool.give_back(conn)
        clock.advance(31.0)

        barrier = threading.Barrier(8)
        results = []
        results_lock = threading.Lock()

        def run_reaper():
            barrier.wait()
            reaped = pool.reap_once()
            with results_lock:
                results.append(reaped)

        threads = [threading.Thread(target=run_reaper) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(results), 5)        # exactly 5 reaped in total
        self.assertEqual(sorted(results).count(0), 7)
        for conn in victims:
            self.assertEqual(conn.close_calls, 1)  # no double close
        stats = pool.stats()
        self.assertEqual(stats["idle"], 0)
        self.assertEqual(stats["closed"], 5)


class ProtocolTest(unittest.TestCase):
    def test_double_give_back_is_rejected_and_counts_stay_consistent(self):
        pool, _, _ = make_pool()
        conn = pool.borrow()
        pool.give_back(conn)
        with self.assertRaises(PoolError):
            pool.give_back(conn)
        stats = pool.stats()
        self.assertEqual(stats["idle"], 1)
        self.assertEqual(stats["borrowed"], 0)
        self.assertEqual(stats["total"], 1)


class ConcurrencyStressTest(unittest.TestCase):
    """Hammer borrow/give_back from many threads while the reaper runs.

    Asserts the core safety property: no borrower ever observes a closed
    connection, and the counters reconcile exactly at the end.
    """

    def test_no_miskill_under_contention(self):
        created = []
        created_lock = threading.Lock()

        def factory():
            conn = FakeConnection()
            with created_lock:
                created.append(conn)
            return conn

        pool = ConnectionPool(factory, idle_timeout=0.02, max_idle=4)
        errors = []
        errors_lock = threading.Lock()
        stop = threading.Event()

        def worker():
            while not stop.is_set():
                conn = pool.borrow()
                try:
                    conn.execute()       # raises if the reaper mis-killed
                except ConnectionClosedError as exc:
                    with errors_lock:
                        errors.append(exc)
                finally:
                    try:
                        pool.give_back(conn)
                    except PoolError as exc:
                        with errors_lock:
                            errors.append(exc)

        def reaper():
            while not stop.is_set():
                pool.reap_once()
                time.sleep(0.001)

        threads = ([threading.Thread(target=worker) for _ in range(8)]
                   + [threading.Thread(target=reaper) for _ in range(2)])
        for t in threads:
            t.start()
        time.sleep(0.5)
        stop.set()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        stats = pool.stats()
        self.assertEqual(stats["borrowed"], 0)
        self.assertEqual(stats["total"], stats["idle"])
        self.assertLessEqual(stats["idle"], 4)
        # Global invariant: every created connection is either alive in the
        # pool or accounted for as closed.
        self.assertEqual(len(created), stats["total"] + stats["closed"])


if __name__ == "__main__":
    unittest.main()
