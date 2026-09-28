"""Thread-safe connection pool (stdlib only).

Features:
  - borrow / return with a hard cap on total connections (max_size)
  - cap on idle connections (max_idle); overflow is closed on return
  - background reaper closes only connections that are BOTH idle and
    past idle_timeout (never in-use connections)
  - validation before every borrow: connections whose peer has closed
    are discarded and replaced, never lent out
  - atomic idle -> in-use hand-off under one lock, so the reaper and a
    borrower can never hold the same connection
  - statistics: connect count, reuse count/rate, reaped, validation
    failures, idle-overflow closes
"""

from __future__ import annotations

import socket
import threading
import time
from collections import deque


class PoolError(Exception):
    pass


class PoolExhausted(PoolError):
    """No connection available within the acquire timeout."""


class PoolClosed(PoolError):
    """The pool has been closed."""


def default_validate(conn) -> bool:
    """Return True if the connection looks usable.

    Sockets: an orderly peer shutdown makes a non-blocking peek return
    b''; a reset raises ConnectionResetError. No pending data -> alive.
    Non-socket objects: a ``closed`` attribute is honoured if present.
    """
    if isinstance(conn, socket.socket):
        try:
            data = conn.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        except BlockingIOError:
            return True
        except (ConnectionResetError, BrokenPipeError, OSError):
            return False
        return data != b""
    closed = getattr(conn, "closed", None)
    if closed is not None:
        return not closed
    return True


class PoolStats:
    """All counters are mutated under the pool lock; read via snapshot()."""

    def __init__(self):
        self.created = 0               # factory calls that succeeded (建连次数)
        self.reused = 0                # borrows served from the idle list
        self.returned = 0              # connections handed back
        self.reaped = 0                # idle+expired connections closed
        self.validation_failures = 0   # stale connections discarded at borrow
        self.idle_overflow_closed = 0  # closed on return because idle was full
        self.timeouts = 0              # acquire() calls that raised PoolExhausted

    @property
    def borrows(self):
        return self.created + self.reused

    @property
    def reuse_rate(self):
        return self.reused / self.borrows if self.borrows else 0.0


class PooledConnection:
    """Borrowed-connection handle. close() returns it to the pool."""

    __slots__ = ("_pool", "raw", "_released")

    def __init__(self, pool, raw):
        self._pool = pool
        self.raw = raw
        self._released = False

    def close(self):
        if not self._released:
            self._released = True
            self._pool.release(self.raw)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __getattr__(self, name):
        return getattr(self.raw, name)


class ConnectionPool:
    def __init__(self, factory, *, max_size=10, max_idle=None,
                 idle_timeout=60.0, validate=default_validate,
                 reap_interval=None, clock=time.monotonic):
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        if max_idle is None:
            max_idle = max_size
        if not 0 <= max_idle <= max_size:
            raise ValueError("max_idle must be within [0, max_size]")
        self._factory = factory
        self._max_size = max_size
        self._max_idle = max_idle
        self._idle_timeout = idle_timeout
        self._validate = validate or (lambda conn: True)
        self._clock = clock

        self._idle = deque()          # [(conn, idle_since), ...]  (LIFO pop)
        self._in_use = set()          # connections currently lent out
        self._creating = 0            # factory calls in flight (slot reserved)
        self._total = 0               # idle + in_use + creating
        self._closed = False

        self._cond = threading.Condition()
        self._reap_guard = threading.Lock()  # reaper reentrancy guard
        self._stop = threading.Event()
        self.stats = PoolStats()

        self._reaper = None
        if reap_interval is not None:
            self._reaper = threading.Thread(
                target=self._reaper_loop, args=(reap_interval,),
                name="connection-pool-reaper", daemon=True)
            self._reaper.start()

    # ---------------------------------------------------------------- borrow

    def acquire(self, timeout=None) -> PooledConnection:
        """Borrow a connection. Raises PoolExhausted on timeout."""
        deadline = None if timeout is None else self._clock() + timeout
        while True:
            conn = None
            create = False
            with self._cond:
                if self._closed:
                    raise PoolClosed("pool is closed")
                if self._idle:
                    # Atomic hand-off: the connection leaves the idle list and
                    # enters in_use under the same lock, so the reaper can
                    # never see (or close) a connection being borrowed.
                    conn = self._idle.pop()[0]
                    self._in_use.add(conn)
                elif self._total < self._max_size:
                    self._total += 1      # reserve a slot before connecting
                    self._creating += 1
                    create = True
                else:
                    if deadline is None:
                        self._cond.wait()
                        continue
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        self.stats.timeouts += 1
                        raise PoolExhausted(
                            "no connection available (max_size=%d)"
                            % self._max_size)
                    self._cond.wait(remaining)
                    continue

            if conn is not None:
                # Validate outside the lock; conn is already marked in-use.
                if self._validate(conn):
                    with self._cond:
                        self.stats.reused += 1
                    return PooledConnection(self, conn)
                self._close_safely(conn)
                with self._cond:
                    self._in_use.discard(conn)
                    self._total -= 1
                    self.stats.validation_failures += 1
                    self._cond.notify()
                continue

            # create path (slot already reserved)
            try:
                new_conn = self._factory()
            except Exception:
                with self._cond:
                    self._total -= 1
                    self._creating -= 1
                    self._cond.notify()
                raise
            with self._cond:
                self._creating -= 1
                if self._closed:
                    self._total -= 1
                    self._cond.notify()
                    close_new = True
                else:
                    self._in_use.add(new_conn)
                    self.stats.created += 1
                    close_new = False
            if close_new:
                self._close_safely(new_conn)
                raise PoolClosed("pool is closed")
            return PooledConnection(self, new_conn)

    # ---------------------------------------------------------------- return

    def release(self, conn):
        """Return a connection (accepts a PooledConnection or its raw conn)."""
        if isinstance(conn, PooledConnection):
            conn.close()
            return
        with self._cond:
            if conn not in self._in_use:
                raise PoolError("connection was not checked out from this pool")
            self._in_use.discard(conn)
            self.stats.returned += 1
            over_idle = len(self._idle) >= self._max_idle
            if self._closed or over_idle:
                self._total -= 1
                if over_idle and not self._closed:
                    self.stats.idle_overflow_closed += 1
                close_it = True
            else:
                self._idle.append((conn, self._clock()))
                close_it = False
            self._cond.notify()
        if close_it:
            self._close_safely(conn)

    # ---------------------------------------------------------------- reaper

    def _reap_expired(self):
        """Close connections that are BOTH idle and past idle_timeout.

        Reentrant-safe: a concurrent run returns immediately instead of
        piling up. Returns the number of connections closed.
        """
        if not self._reap_guard.acquire(blocking=False):
            return 0
        try:
            now = self._clock()
            with self._cond:
                expired = [(c, ts) for c, ts in self._idle
                           if now - ts > self._idle_timeout]
                if expired:
                    expired_ids = {id(c) for c, _ in expired}
                    self._idle = deque(
                        (c, ts) for c, ts in self._idle
                        if id(c) not in expired_ids)
                    self._total -= len(expired)
                    self.stats.reaped += len(expired)
            # Close outside the lock. Each conn was atomically removed from
            # the idle list above, so no borrower and no other reaper run can
            # ever see it again -> it is closed exactly once.
            for conn, _ in expired:
                self._close_safely(conn)
            return len(expired)
        finally:
            self._reap_guard.release()

    def _reaper_loop(self, interval):
        while not self._stop.wait(interval):
            self._reap_expired()

    # ---------------------------------------------------------------- misc

    def close(self):
        """Close the pool and all idle connections.

        In-use connections are closed when they are released.
        """
        self._stop.set()
        with self._cond:
            if self._closed:
                return
            self._closed = True
            idle = [c for c, _ in self._idle]
            self._idle.clear()
            self._total -= len(idle)
            self._cond.notify_all()
        for conn in idle:
            self._close_safely(conn)
        if self._reaper is not None:
            self._reaper.join(timeout=2.0)

    def snapshot(self):
        with self._cond:
            s = self.stats
            return {
                "created": s.created,
                "reused": s.reused,
                "borrows": s.borrows,
                "reuse_rate": s.reuse_rate,
                "returned": s.returned,
                "reaped": s.reaped,
                "validation_failures": s.validation_failures,
                "idle_overflow_closed": s.idle_overflow_closed,
                "timeouts": s.timeouts,
                "idle": len(self._idle),
                "in_use": len(self._in_use),
                "total": self._total,
            }

    def _check_invariants(self):
        """Internal consistency assertions, used by the test-suite."""
        with self._cond:
            idle_conns = [c for c, _ in self._idle]
            assert len({id(c) for c in idle_conns}) == len(idle_conns), \
                "duplicate connection in idle list"
            assert not ({id(c) for c in idle_conns}
                        & {id(c) for c in self._in_use}), \
                "connection both idle and in use"
            assert self._total == len(idle_conns) + len(self._in_use) \
                + self._creating, "total counter drifted"
            assert len(idle_conns) <= self._max_idle, "idle overflow"
            assert self._total <= self._max_size, "max_size exceeded"

    @staticmethod
    def _close_safely(conn):
        try:
            conn.close()
        except Exception:
            pass
