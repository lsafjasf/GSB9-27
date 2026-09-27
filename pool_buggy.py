"""Buggy connection pool -- kept only to reproduce the idle-reaper mis-kill.

Bugs (fixed in pool_fixed.py):
  A. Idle time is measured from *borrow* time, and the reaper also sweeps
     entries in the borrowed map, so a connection that has been checked out
     for longer than `idle_timeout` is closed while still in use.
  B. Scan-and-close is not atomic: the reaper collects victims without
     holding the pool lock, then closes them afterwards. A connection that
     gets borrowed (or returned) between the scan and the close is closed
     underneath its new owner.
"""

import threading
import time


class ConnectionClosedError(Exception):
    """Raised when an operation is attempted on a closed connection."""


class BuggyConnectionPool:
    def __init__(self, factory, idle_timeout=30.0, max_idle=10,
                 clock=time.monotonic):
        self._factory = factory
        self._idle_timeout = idle_timeout
        self._max_idle = max_idle
        self._clock = clock
        self._idle = []           # list of (conn, borrow_timestamp)
        self._borrowed_at = {}    # conn -> borrow timestamp
        self._lock = threading.Lock()

    def borrow(self):
        with self._lock:
            if self._idle:
                conn, _ = self._idle.pop()
            else:
                conn = self._factory()
            self._borrowed_at[conn] = self._clock()
            return conn

    def give_back(self, conn):
        with self._lock:
            # Bug A: the *borrow* timestamp is stored as the idle timestamp.
            ts = self._borrowed_at.pop(conn, self._clock())
            self._idle.append((conn, ts))

    # The scan/close split below mirrors the original code structure and
    # lets tests interleave borrow/give_back deterministically between the
    # two phases (this is exactly the window the background thread hits in
    # production).
    def _scan(self):
        now = self._clock()
        victims = []
        for conn, ts in list(self._idle):       # no lock held
            if now - ts > self._idle_timeout:
                victims.append(conn)
        # Bug A: borrowed connections are swept too, by borrow age.
        for conn, ts in list(self._borrowed_at.items()):
            if now - ts > self._idle_timeout:
                victims.append(conn)
        return victims

    def _close(self, victims):
        for conn in victims:                    # Bug B: no re-validation
            conn.close()
            self._discard(conn)

    def _discard(self, conn):
        with self._lock:
            self._idle = [(c, t) for c, t in self._idle if c is not conn]
            self._borrowed_at.pop(conn, None)

    def reap_once(self):
        self._close(self._scan())
