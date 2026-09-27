"""Self-tests for connpool.py. Standard library only.

Run:  python3 test_connpool.py        (or: python3 -m unittest -v)
"""

import random
import socket
import threading
import time
import unittest

from connpool import (
    ConnectionPool,
    PoolClosed,
    PoolExhausted,
    default_socket_validator,
)


# --------------------------------------------------------------------- helpers

class EchoServer:
    """Threaded TCP echo server used as the 'remote' end in tests."""

    def __init__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(64)
        self.port = self._srv.getsockname()[1]
        self._clients = []
        self._lock = threading.Lock()
        self._closed = False
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while True:
            try:
                client, _ = self._srv.accept()
            except OSError:
                return
            with self._lock:
                if self._closed:
                    client.close()
                    continue
                self._clients.append(client)
            threading.Thread(target=self._echo_loop, args=(client,), daemon=True).start()

    def _echo_loop(self, client):
        try:
            while True:
                data = client.recv(4096)
                if not data:
                    return
                client.sendall(data)
        except OSError:
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass

    def close_all_clients(self):
        """Simulate the peer dropping connections (FIN sent immediately)."""
        with self._lock:
            clients, self._clients = self._clients, []
        for c in clients:
            try:
                c.shutdown(socket.SHUT_RDWR)  # FIN even if a thread blocks in recv
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass

    def stop(self):
        with self._lock:
            self._closed = True
        try:
            self._srv.close()
        except OSError:
            pass
        self.close_all_clients()


class TrackedSocket:
    """Client socket wrapper that counts close() calls (double-close /
    leak detector) and delegates everything else to a real TCP socket."""

    def __init__(self, port):
        self._sock = socket.create_connection(("127.0.0.1", port))
        self.close_count = 0
        self._close_lock = threading.Lock()

    def fileno(self):
        return self._sock.fileno()

    def recv(self, n, flags=0):
        return self._sock.recv(n, flags)

    def sendall(self, data):
        return self._sock.sendall(data)

    def close(self):
        with self._close_lock:
            self.close_count += 1
            self._sock.close()


def echo_roundtrip(conn, payload=b"ping"):
    conn.sendall(payload)
    got = b""
    while len(got) < len(payload):
        chunk = conn.recv(4096)
        if not chunk:
            raise ConnectionError("echo server closed the connection")
        got += chunk
    assert got == payload, (got, payload)


# ----------------------------------------------------------------------- tests

class PoolTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = EchoServer()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.registry = []          # every TrackedSocket ever created
        self.registry_lock = threading.Lock()
        self.pool = None

    def tearDown(self):
        if self.pool is not None:
            self.pool.close()

    def factory(self):
        conn = TrackedSocket(self.server.port)
        with self.registry_lock:
            self.registry.append(conn)
        return conn

    def make_pool(self, **kw):
        self.pool = ConnectionPool(self.factory, **kw)
        return self.pool

    def assert_no_leak_no_double_close(self):
        """Global invariant: every created connection is closed exactly
        once, and pool accounting balances out."""
        stats = self.pool.stats()
        self.assertEqual(stats["created"], stats["closed_total"] + stats["total"])
        with self.registry_lock:
            for conn in self.registry:
                self.assertLessEqual(conn.close_count, 1,
                                     "connection closed more than once")

    # ---------------------------------------------------------- basic behavior

    def test_empty_pool_creates_connection(self):
        pool = self.make_pool(max_size=2, idle_timeout=60)
        conn = pool.borrow(timeout=1)
        self.assertEqual(pool.stats()["created"], 1)
        self.assertEqual(pool.stats()["reused"], 0)
        echo_roundtrip(conn)
        pool.give_back(conn)

    def test_borrow_return_reuse_and_stats(self):
        pool = self.make_pool(max_size=2, idle_timeout=60)
        c1 = pool.borrow(timeout=1)
        echo_roundtrip(c1)
        pool.give_back(c1)
        c2 = pool.borrow(timeout=1)
        self.assertIs(c1, c2, "second borrow should reuse the idle connection")
        echo_roundtrip(c2)
        pool.give_back(c2)
        stats = pool.stats()
        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["reused"], 1)
        self.assertEqual(stats["borrows"], 2)
        self.assertAlmostEqual(stats["reuse_rate"], 0.5)
        self.assertEqual(stats["idle"], 1)

    # ------------------------------------------- peer-close (must not be lent)

    def test_peer_closed_connection_is_never_lent(self):
        """Reproducible peer-close case: the server drops the connection
        while it sits idle in the pool; the next borrow must detect it,
        discard it and hand out a fresh, working connection."""
        pool = self.make_pool(max_size=2, idle_timeout=60)
        c1 = pool.borrow(timeout=1)
        echo_roundtrip(c1)
        pool.give_back(c1)

        self.server.close_all_clients()  # peer closes -> FIN on the wire

        # Wait until the FIN is observable on the client side.
        deadline = time.monotonic() + 3
        while default_socket_validator(c1) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(default_socket_validator(c1),
                         "validator should report the peer-closed socket as dead")

        c2 = pool.borrow(timeout=1)
        self.assertIsNot(c1, c2, "peer-closed connection must not be lent out")
        echo_roundtrip(c2, b"still-works")  # and the replacement really works
        pool.give_back(c2)

        stats = pool.stats()
        self.assertEqual(stats["validation_failures"], 1)
        self.assertEqual(stats["created"], 2)
        self.assertEqual(c1.close_count, 1)

    # ---------------------------------------------------- reaping (false-kill)

    def test_reap_expired_idle_connections(self):
        pool = self.make_pool(max_size=4, idle_timeout=0.2)
        conn = pool.borrow(timeout=1)
        pool.give_back(conn)
        time.sleep(0.4)
        self.assertEqual(pool.reap(), 1, "expired idle connection must be reaped")
        stats = pool.stats()
        self.assertEqual(stats["reaped"], 1)
        self.assertEqual(stats["total"], 0)
        # Next borrow must create a brand-new connection.
        conn2 = pool.borrow(timeout=1)
        self.assertIsNot(conn, conn2)
        self.assertEqual(pool.stats()["created"], 2)
        pool.give_back(conn2)

    def test_reap_does_not_kill_fresh_idle(self):
        """False-kill test: an idle connection younger than idle_timeout
        must survive the reaper and be reused afterwards."""
        pool = self.make_pool(max_size=4, idle_timeout=60)
        conn = pool.borrow(timeout=1)
        pool.give_back(conn)
        self.assertEqual(pool.reap(), 0, "fresh idle connection must not be reaped")
        self.assertEqual(pool.stats()["reaped"], 0)
        again = pool.borrow(timeout=1)
        self.assertIs(conn, again, "surviving connection must be reused")
        pool.give_back(again)

    def test_reap_never_touches_checked_out(self):
        """False-kill test: a checked-out connection is never reclaimed,
        even when it has been held far longer than idle_timeout."""
        pool = self.make_pool(max_size=2, idle_timeout=0.1)
        conn = pool.borrow(timeout=1)
        time.sleep(0.3)          # held way beyond idle_timeout
        self.assertEqual(pool.reap(), 0)
        echo_roundtrip(conn, b"alive")  # still fully usable
        pool.give_back(conn)
        self.assertEqual(conn.close_count, 0)

    # ------------------------------------------------------------- leak tests

    def test_unreturned_connection_exhausts_pool(self):
        """Leak test: a borrower that never returns its connection must
        not block others forever -- borrow times out with PoolExhausted."""
        pool = self.make_pool(max_size=1, idle_timeout=60)
        conn = pool.borrow(timeout=1)
        with self.assertRaises(PoolExhausted):
            pool.borrow(timeout=0.2)
        pool.give_back(conn)     # once returned, the pool recovers
        again = pool.borrow(timeout=1)
        self.assertIs(conn, again)
        pool.give_back(again)

    def test_invalidate_frees_slot(self):
        pool = self.make_pool(max_size=1, idle_timeout=60)
        conn = pool.borrow(timeout=1)
        pool.invalidate(conn)    # borrower reports the connection as broken
        self.assertEqual(conn.close_count, 1)
        fresh = pool.borrow(timeout=1)   # slot was freed immediately
        self.assertIsNot(conn, fresh)
        pool.give_back(fresh)
        stats = pool.stats()
        self.assertEqual(stats["invalidated"], 1)
        self.assertEqual(stats["created"], 2)

    def test_max_size_limit_enforced(self):
        pool = self.make_pool(max_size=2, idle_timeout=60)
        a = pool.borrow(timeout=1)
        b = pool.borrow(timeout=1)
        with self.assertRaises(PoolExhausted):
            pool.borrow(timeout=0.2)
        self.assertEqual(pool.stats()["total"], 2)
        pool.give_back(a)
        pool.give_back(b)

    # ------------------------------------------------------------- edge cases

    def test_idle_overflow_closed_on_return(self):
        """Returning more connections than max_idle closes the extras."""
        pool = self.make_pool(max_size=3, max_idle=1, idle_timeout=60)
        conns = [pool.borrow(timeout=1) for _ in range(3)]
        for c in conns:
            pool.give_back(c)
        stats = pool.stats()
        self.assertEqual(stats["idle"], 1)
        self.assertEqual(stats["overflow_closed"], 2)
        self.assertEqual(stats["total"], 1)
        closed = sorted(c.close_count for c in conns)
        self.assertEqual(closed, [0, 1, 1])

    def test_double_return_rejected(self):
        pool = self.make_pool(max_size=1, idle_timeout=60)
        conn = pool.borrow(timeout=1)
        pool.give_back(conn)
        with self.assertRaises(ValueError):
            pool.give_back(conn)
        self.assertEqual(conn.close_count, 0)

    def test_close_pool(self):
        pool = self.make_pool(max_size=2, idle_timeout=60)
        conn = pool.borrow(timeout=1)
        pool.give_back(conn)
        pool.close()
        self.assertEqual(conn.close_count, 1)
        with self.assertRaises(PoolClosed):
            pool.borrow(timeout=0.1)

    def test_close_unblocks_waiters(self):
        pool = self.make_pool(max_size=1, idle_timeout=60)
        conn = pool.borrow(timeout=1)
        errors = []

        def waiter():
            try:
                pool.borrow(timeout=10)
            except PoolClosed:
                errors.append("PoolClosed")

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.1)
        pool.close()
        t.join(timeout=3)
        self.assertEqual(errors, ["PoolClosed"])
        pool.give_back(conn)  # returned after close -> closed, not pooled
        self.assertEqual(conn.close_count, 1)

    # ------------------------------------------- concurrency & invariants

    def test_concurrent_borrow_return_invariant(self):
        """Many threads borrow/return while the reaper runs. Invariant:
        a connection is never held by two threads at the same time."""
        pool = self.make_pool(max_size=4, idle_timeout=0.5, reap_interval=0.02)
        threads_n, iters = 8, 25
        held = {}
        held_lock = threading.Lock()
        errors = []

        def worker():
            try:
                for _ in range(iters):
                    conn = pool.borrow(timeout=10)
                    with held_lock:
                        if conn in held:
                            errors.append("connection held by two parties: %r" % conn)
                        held[conn] = threading.get_ident()
                    echo_roundtrip(conn, b"x" * random.randint(1, 64))
                    time.sleep(random.random() * 0.002)
                    with held_lock:
                        held.pop(conn, None)
                    pool.give_back(conn)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker) for _ in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [])
        stats = pool.stats()
        self.assertEqual(stats["borrows"], threads_n * iters)
        self.assertEqual(stats["created"] + stats["reused"], stats["borrows"])
        self.assertLessEqual(stats["created"], 4)
        self.assertEqual(stats["checked_out"], 0)
        self.assertEqual(stats["idle"], stats["total"])
        self.assertGreater(stats["reuse_rate"], 0.9)
        print("\n[concurrent] stats:", stats)
        self.assert_no_leak_no_double_close()

    def test_reaper_reentrancy_no_double_close(self):
        """Manual reap() calls racing the background reaper and each
        other must never close the same connection twice, and must never
        race a borrower for the same connection."""
        # Fewer borrowers than slots: some connections sit idle long
        # enough to expire, so the racing reapers have real work to do.
        pool = self.make_pool(max_size=8, idle_timeout=0.05, reap_interval=0.01)
        # Pre-warm the pool: 8 idle connections, only ~2 stay hot via
        # reuse; the rest expire and must be reaped exactly once each.
        warm = [pool.borrow(timeout=1) for _ in range(8)]
        for c in warm:
            pool.give_back(c)
        stop = threading.Event()
        errors = []

        def borrower():
            try:
                while not stop.is_set():
                    conn = pool.borrow(timeout=5)
                    time.sleep(random.random() * 0.005)
                    pool.give_back(conn)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        def reaper():
            try:
                while not stop.is_set():
                    pool.reap()  # manual reentrant reaping
                    time.sleep(0.003)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = ([threading.Thread(target=borrower) for _ in range(2)] +
                   [threading.Thread(target=reaper) for _ in range(3)])
        for t in threads:
            t.start()
        time.sleep(1.5)
        stop.set()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        pool.close()
        stats = pool.stats()
        self.assertGreater(stats["reaped"], 0, "reaper should have done some work")
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["created"], stats["closed_total"],
                         "every created connection closed exactly once")
        with self.registry_lock:
            for conn in self.registry:
                self.assertEqual(conn.close_count, 1,
                                 "double close detected (reaper reentrancy bug)")
        print("\n[reentrancy] stats:", stats)

    # ------------------------------------------------------------- accounting

    def test_stats_accounting_invariant(self):
        """created == closed_total + total must hold at every quiescent
        point (no leaks, no double closes) across all operations."""
        pool = self.make_pool(max_size=3, max_idle=2, idle_timeout=0.1)
        c1 = pool.borrow(timeout=1)
        c2 = pool.borrow(timeout=1)
        pool.give_back(c1)
        pool.give_back(c2)
        c3 = pool.borrow(timeout=1)   # reuse
        pool.invalidate(c3)
        time.sleep(0.25)
        pool.reap()
        stats = pool.stats()
        self.assertEqual(stats["created"], stats["closed_total"] + stats["total"])
        self.assert_no_leak_no_double_close()
        print("\n[accounting] stats:", stats)


if __name__ == "__main__":
    unittest.main(verbosity=2)
