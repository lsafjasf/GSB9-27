"""Thread-safe connection pool with idle reaping and liveness validation.

Standard library only (Python 3.8+).

Design invariants:
  * A connection is always in exactly one state: idle (in the pool),
    checked out (held by a borrower), or closed. Transitions happen
    under a single condition lock, so a connection can never be held
    by two parties at once (e.g. reaper + borrower).
  * The reaper only ever touches connections that are *in the idle set*
    and whose idle time exceeds idle_timeout. Checked-out connections
    are never reclaimed.
  * Every idle connection is validated before it is handed out; a
    connection whose peer has closed is discarded and replaced.
"""

from __future__ import annotations

import select
import socket
import threading
import time
from collections import deque
from contextlib import contextmanager


class PoolClosed(Exception):
    """Raised when borrowing from a closed pool."""


class PoolExhausted(Exception):
    """Raised when no connection becomes available within the timeout."""


def default_socket_validator(conn) -> bool:
    """Return True if the connection still looks usable.

    A socket whose peer has closed becomes readable, and a peeked recv
    then returns b''. Pending unread application data counts as alive.
    Non-blocking: never sleeps.
    """
    try:
        readable, _, _ = select.select([conn], [], [], 0)
    except (OSError, ValueError):
        return False
    if not readable:
        return True
    try:
        data = conn.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
    except (BlockingIOError, InterruptedError):
        return True
    except OSError:
        return False
    return len(data) > 0


class _IdleEntry:
    __slots__ = ("conn", "since")

    def __init__(self, conn):
        self.conn = conn
        self.since = time.monotonic()


class ConnectionPool:
    """A bounded, thread-safe pool of reusable connections.

    Parameters:
        factory:       zero-arg callable returning a new connection.
        validator:     callable(conn) -> bool, checked before each reuse.
                       Defaults to a non-blocking socket liveness probe.
        max_size:      hard cap on total connections (idle + checked out).
        max_idle:      cap on idle connections kept for reuse; connections
                       returned beyond this are closed immediately.
                       Defaults to max_size.
        idle_timeout:  seconds after which an *idle* connection may be reaped.
        reap_interval: if given, start a background daemon thread that calls
                       reap() every this many seconds.
    """

    def __init__(self, factory, *, validator=None, max_size=8, max_idle=None,
                 idle_timeout=30.0, reap_interval=None):
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        if max_idle is not None and max_idle < 0:
            raise ValueError("max_idle must be >= 0")
        if idle_timeout <= 0:
            raise ValueError("idle_timeout must be > 0")
        self._factory = factory
        self._validator = validator or default_socket_validator
        self._max_size = max_size
        self._max_idle = max_size if max_idle is None else max_idle
        self._idle_timeout = idle_timeout

        self._cond = threading.Condition()
        self._idle = deque()          # of _IdleEntry, oldest at the left
        self._checked_out = set()     # of conn
        self._total = 0               # idle + checked out + reserved
        self._closed = False

        # statistics (all mutated under self._cond)
        self._created = 0
        self._reused = 0
        self._returned = 0
        self._reaped = 0
        self._validation_failures = 0
        self._overflow_closed = 0
        self._invalidated = 0
        self._closed_count = 0        # every physical close, for leak accounting

        self._stop_event = threading.Event()
        self._reaper_thread = None
        if reap_interval is not None:
            self.start_reaper(reap_interval)

    def borrow(self, timeout=None):
        """Return a live connection, creating one if needed.

        Blocks up to timeout seconds (forever if None) when the pool
        is at max_size and nothing is idle. Raises PoolExhausted on
        timeout, PoolClosed if the pool is closed.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while True:
                if self._closed:
                    raise PoolClosed("pool is closed")
                # Reuse the most recently returned idle connection.
                while self._idle:
                    entry = self._idle.pop()
                    if self._validator(entry.conn):
                        self._checked_out.add(entry.conn)
                        self._reused += 1
                        return entry.conn
                    # Peer closed (or otherwise dead): drop and try next.
                    self._total -= 1
                    self._validation_failures += 1
                    self._close_locked(entry.conn)
                    self._cond.notify()  # a slot freed up
                if self._total < self._max_size:
                    self._total += 1  # reserve a slot, connect outside the lock
                    break
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise PoolExhausted(
                        "no connection available within timeout "
                        "(total=%d, checked_out=%d)" % (self._total, len(self._checked_out))
                    )
                self._cond.wait(remaining)

        conn = None
        try:
            conn = self._factory()
        except Exception:
            with self._cond:
                self._total -= 1
                self._cond.notify()
            raise
        with self._cond:
            if self._closed:
                self._total -= 1
                self._cond.notify()
                try:
                    conn.close()
                except Exception:
                    pass
                raise PoolClosed("pool is closed")
            self._checked_out.add(conn)
            self._created += 1
        return conn

    def give_back(self, conn):
        """Return a borrowed connection to the pool.

        If the idle set is already at max_idle (or the pool is closed),
        the connection is closed instead of being kept.
        """
        with self._cond:
            if conn not in self._checked_out:
                raise ValueError("connection was not checked out from this pool")
            self._checked_out.discard(conn)
            self._returned += 1
            if self._closed:
                self._total -= 1
                self._close_locked(conn)
            elif len(self._idle) >= self._max_idle:
                self._total -= 1
                self._overflow_closed += 1
                self._close_locked(conn)
            else:
                self._idle.append(_IdleEntry(conn))
            self._cond.notify()

    def invalidate(self, conn):
        """Drop a borrowed connection known to be broken, freeing its slot."""
        with self._cond:
            if conn not in self._checked_out:
                raise ValueError("connection was not checked out from this pool")
            self._checked_out.discard(conn)
            self._total -= 1
            self._invalidated += 1
            self._close_locked(conn)
            self._cond.notify()

    @contextmanager
    def connection(self, timeout=None):
        """Context manager: borrow on enter, give back on exit."""
        conn = self.borrow(timeout=timeout)
        try:
            yield conn
        finally:
            self.give_back(conn)

    def reap(self):
        """Close idle connections whose idle time exceeded idle_timeout.

        Reentrant-safe: the idle-set mutation happens atomically under
        the pool lock, so concurrent/overlapping reap() calls (manual or
        from the background thread) can never close the same connection
        twice, and never touch checked-out connections.
        Returns the number of connections reaped.
        """
        now = time.monotonic()
        with self._cond:
            survivors = deque()
            expired = []
            while self._idle:
                entry = self._idle.popleft()
                if now - entry.since >= self._idle_timeout:
                    expired.append(entry)
                else:
                    survivors.append(entry)
            self._idle = survivors
            for entry in expired:
                self._total -= 1
                self._reaped += 1
                self._close_locked(entry.conn)
            if expired:
                self._cond.notify_all()
            return len(expired)

    def start_reaper(self, interval):
        """Start (or restart) the background reaper thread."""
        self.stop_reaper()
        self._stop_event.clear()

        def loop():
            while not self._stop_event.wait(interval):
                try:
                    self.reap()
                except Exception:
                    pass  # the reaper must never die silently

        self._reaper_thread = threading.Thread(
            target=loop, name="connpool-reaper", daemon=True)
        self._reaper_thread.start()

    def stop_reaper(self):
        self._stop_event.set()
        thread = self._reaper_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        self._reaper_thread = None

    def stats(self):
        """Return a snapshot dict of counters and gauges.

        Invariant: created == closed_total + total (no leaks, no double
        closes) whenever no borrower is between borrow() and give_back().
        """
        with self._cond:
            borrows = self._created + self._reused
            return {
                "created": self._created,            # physical connections opened
                "reused": self._reused,              # borrows served from the idle set
                "borrows": borrows,
                "reuse_rate": self._reused / borrows if borrows else 0.0,
                "returned": self._returned,
                "reaped": self._reaped,
                "validation_failures": self._validation_failures,
                "overflow_closed": self._overflow_closed,
                "invalidated": self._invalidated,
                "closed_total": self._closed_count,  # physical connections closed
                "idle": len(self._idle),
                "checked_out": len(self._checked_out),
                "total": self._total,
                "max_size": self._max_size,
            }

    def close(self):
        """Close the pool: reaper stops, idle connections are closed,
        pending and future borrows fail. Checked-out connections are
        closed when (and if) they are returned."""
        self.stop_reaper()
        with self._cond:
            self._closed = True
            while self._idle:
                entry = self._idle.popleft()
                self._total -= 1
                self._close_locked(entry.conn)
            self._cond.notify_all()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _close_locked(self, conn):
        """Close a connection. Caller must hold self._cond and must have
        already removed the connection from every pool structure."""
        self._closed_count += 1
        try:
            conn.close()
        except Exception:
            pass
