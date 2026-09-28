"""Self-tests for pool.py. Run:  python3 -m unittest test_pool -v"""

import socket
import threading
import time
import unittest

from pool import (ConnectionPool, PoolClosed, PoolError, PoolExhausted,
                  default_validate)


class FakeConn:
    """Close-counting fake connection (leak / double-close detection)."""

    def __init__(self):
        self.closed = False
        self.close_count = 0

    def close(self):
        self.closed = True
        self.close_count += 1


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def fake_factory_maker():
    made = []

    def factory():
        conn = FakeConn()
        made.append(conn)
        return conn

    return factory, made


class BasicPoolTests(unittest.TestCase):
    def test_borrow_return_reuse_and_stats(self):
        factory, made = fake_factory_maker()
        pool = ConnectionPool(factory, max_size=4)
        a = pool.acquire()
        a.close()                      # return to pool
        b = pool.acquire()
        self.assertIs(a.raw, b.raw)    # same connection reused
        snap = pool.snapshot()
        self.assertEqual(snap["created"], 1)          # 建连次数
        self.assertEqual(snap["reused"], 1)
        self.assertAlmostEqual(snap["reuse_rate"], 0.5)
        pool.close()

    def test_empty_pool_creates_on_demand(self):
        factory, made = fake_factory_maker()
        pool = ConnectionPool(factory, max_size=2)
        self.assertEqual(pool.snapshot()["total"], 0)  # 池为空
        c = pool.acquire()
        self.assertEqual(len(made), 1)
        self.assertEqual(pool.snapshot()["in_use"], 1)
        c.close()
        pool.close()

    def test_max_total_cap_blocks_then_times_out(self):
        factory, _ = fake_factory_maker()
        pool = ConnectionPool(factory, max_size=1)
        pool.acquire()
        with self.assertRaises(PoolExhausted):
            pool.acquire(timeout=0.05)
        self.assertEqual(pool.snapshot()["timeouts"], 1)
        pool.close()

    def test_idle_overflow_closed_on_return(self):
        factory, made = fake_factory_maker()
        pool = ConnectionPool(factory, max_size=2, max_idle=1)
        c1, c2 = pool.acquire(), pool.acquire()
        c1.close()
        c2.close()                     # idle already full -> closed
        self.assertEqual(made[1].close_count, 1)
        self.assertEqual(made[0].close_count, 0)
        snap = pool.snapshot()
        self.assertEqual(snap["idle_overflow_closed"], 1)
        self.assertEqual(snap["idle"], 1)
        pool._check_invariants()
        pool.close()

    def test_double_release_rejected(self):
        factory, _ = fake_factory_maker()
        pool = ConnectionPool(factory, max_size=1)
        c = pool.acquire()
        raw = c.raw
        c.close()
        c.close()                      # idempotent on the handle
        with self.assertRaises(PoolError):
            pool.release(raw)          # direct double release -> error
        pool.close()


class ReaperTests(unittest.TestCase):
    def test_reap_only_expired_idle_no_false_kill(self):
        """误杀测试: fresh idle connections must survive the reaper."""
        factory, made = fake_factory_maker()
        clock = FakeClock()
        pool = ConnectionPool(factory, max_size=4, idle_timeout=5.0,
                              clock=clock)
        old = pool.acquire()           # made[0]
        fresh = pool.acquire()         # made[1]
        old.close()                    # idle since t=0
        clock.advance(10.0)            # now t=10, old is stale
        fresh.close()                  # idle since t=10, still fresh
        self.assertEqual(pool._reap_expired(), 1)
        self.assertEqual(made[0].close_count, 1, "stale conn must be reaped")
        self.assertEqual(made[1].close_count, 0, "fresh conn falsely killed!")
        again = pool.acquire()         # fresh conn still usable
        self.assertIs(again.raw, made[1])
        again.close()
        pool._check_invariants()
        pool.close()

    def test_in_use_connection_never_reaped(self):
        """误杀测试: a checked-out connection is never reclaimed."""
        factory, made = fake_factory_maker()
        clock = FakeClock()
        pool = ConnectionPool(factory, max_size=2, idle_timeout=5.0,
                              clock=clock)
        c = pool.acquire()
        c.close()
        held = pool.acquire()          # check it out...
        clock.advance(100.0)           # ...well past idle_timeout
        self.assertEqual(pool._reap_expired(), 0)
        self.assertEqual(made[0].close_count, 0,
                         "in-use connection was reaped!")
        held.close()                   # idle again, timestamp refreshed
        self.assertEqual(pool._reap_expired(), 0)  # not yet expired
        clock.advance(100.0)
        self.assertEqual(pool._reap_expired(), 1)  # now idle+expired
        pool.close()

    def test_reaper_reentrancy_no_double_close(self):
        """回收任务重入: concurrent reap runs close each conn exactly once."""
        factory, made = fake_factory_maker()
        clock = FakeClock()
        pool = ConnectionPool(factory, max_size=20, idle_timeout=1.0,
                              clock=clock)
        conns = [pool.acquire() for _ in range(20)]
        for c in conns:
            c.close()
        clock.advance(10.0)

        errors = []

        def reap_many():
            try:
                for _ in range(50):
                    pool._reap_expired()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=reap_many) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        closed = sum(c.close_count for c in made)
        self.assertEqual(closed, 20, "expired conns must be closed once each")
        self.assertTrue(all(c.close_count == 1 for c in made),
                        "a connection was closed twice (reaper race)")
        self.assertEqual(pool.snapshot()["reaped"], 20)
        pool._check_invariants()
        pool.close()

    def test_background_reaper_thread(self):
        factory, made = fake_factory_maker()
        pool = ConnectionPool(factory, max_size=2, idle_timeout=0.05,
                              reap_interval=0.02)
        c = pool.acquire()
        c.close()
        deadline = time.monotonic() + 2.0
        while pool.snapshot()["reaped"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(pool.snapshot()["reaped"], 1)
        self.assertEqual(made[0].close_count, 1)
        pool.close()


class ValidationTests(unittest.TestCase):
    def _socket_pool(self, **kw):
        pairs = []

        def factory():
            ours, peer = socket.socketpair()
            pairs.append((ours, peer))
            return ours

        return ConnectionPool(factory, max_size=2, **kw), pairs

    def test_peer_closed_connection_not_lent_out(self):
        """对端关闭: stale socket must be discarded, never borrowed."""
        pool, pairs = self._socket_pool()
        c1 = pool.acquire()
        raw1 = c1.raw
        c1.close()
        pairs[0][1].close()            # peer performs an orderly shutdown
        c2 = pool.acquire()
        self.assertIsNot(c2.raw, raw1, "pool lent out a dead connection")
        self.assertEqual(raw1.close_count if hasattr(raw1, "close_count")
                         else 0, 0)    # raw socket: closed via pool
        snap = pool.snapshot()
        self.assertEqual(snap["validation_failures"], 1)
        self.assertEqual(snap["created"], 2)   # replacement was connected
        # the replacement is genuinely usable
        c2.raw.sendall(b"ping")
        self.assertEqual(pairs[1][1].recv(4), b"ping")
        c2.close()
        pool.close()
        for ours, peer in pairs:
            for s in (ours, peer):
                try:
                    s.close()
                except OSError:
                    pass

    def test_live_socket_reused_no_false_kill(self):
        """误杀测试: healthy idle socket passes validation and is reused."""
        pool, pairs = self._socket_pool()
        c1 = pool.acquire()
        raw1 = c1.raw
        c1.close()
        c2 = pool.acquire()
        self.assertIs(c2.raw, raw1, "healthy connection falsely discarded")
        self.assertEqual(pool.snapshot()["validation_failures"], 0)
        c2.close()
        pool.close()
        for ours, peer in pairs:
            for s in (ours, peer):
                try:
                    s.close()
                except OSError:
                    pass

    def test_default_validate_on_reset_peer(self):
        ours, peer = socket.socketpair()
        self.assertTrue(default_validate(ours))
        peer.close()
        self.assertFalse(default_validate(ours))
        ours.close()


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_borrow_return_reap_invariants(self):
        """并发借还 + 回收: no conn held by two parties, no leaks."""
        factory, made = fake_factory_maker()
        clock = FakeClock()
        clock_lock = threading.Lock()

        def safe_clock():
            with clock_lock:
                return clock.t

        pool = ConnectionPool(factory, max_size=5, max_idle=5,
                              idle_timeout=0.5, clock=safe_clock)
        errors = []
        stop = threading.Event()

        def worker():
            try:
                for _ in range(300):
                    with pool.acquire(timeout=5.0):
                        pass
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def reaper():
            while not stop.is_set():
                with clock_lock:
                    clock.t += 0.1
                pool._reap_expired()
                time.sleep(0.001)

        workers = [threading.Thread(target=worker) for _ in range(8)]
        reap_thread = threading.Thread(target=reaper)
        reap_thread.start()
        for t in workers:
            t.start()
        for t in workers:
            t.join()
        stop.set()
        reap_thread.join()

        self.assertEqual(errors, [])
        pool._check_invariants()
        # 不变量: every connection closed at most once (never held/closed
        # by reaper and borrower simultaneously)
        self.assertTrue(all(c.close_count <= 1 for c in made),
                        "a connection was closed twice")
        # 无泄漏: created == closed + still-idle
        snap = pool.snapshot()
        closed = sum(c.close_count for c in made)
        self.assertEqual(snap["created"], closed + snap["idle"])
        self.assertEqual(snap["in_use"], 0)
        pool.close()
        self.assertTrue(all(c.close_count == 1 for c in made),
                        "leaked connection after pool.close()")

    def test_pool_close_idempotent_and_leak_free(self):
        factory, made = fake_factory_maker()
        pool = ConnectionPool(factory, max_size=3)
        a, b = pool.acquire(), pool.acquire()
        a.close()
        pool.close()
        pool.close()                   # idempotent
        b.close()                      # released after close -> destroyed
        self.assertTrue(all(c.close_count == 1 for c in made))
        with self.assertRaises(PoolClosed):
            pool.acquire(timeout=0.01)


if __name__ == "__main__":
    unittest.main(verbosity=2)
