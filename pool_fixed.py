"""Fixed connection pool with a safe idle reaper. Standard library only.

Synchronization model
---------------------
A single `threading.Lock` (`self._lock`) guards every state transition.
A connection object is always in exactly one of two places:

  * `_idle`     -- deque of (conn, idle_since); available to borrowers and
                   visible to the reaper.
  * `_borrowed` -- set of conns currently checked out; the reaper never
                   touches anything in this set.

The reaper closes a connection only after it has been *removed from `_idle`
under the lock*. From that moment no borrower can ever obtain it, so the
actual (potentially slow) `conn.close()` runs outside the lock without any
risk of closing an in-use connection. Borrowed connections are never
scanned: the reaper does not even look at `_borrowed`.

All counters reported by `stats()` are *derived* from the two containers
while holding the lock, so they can never drift apart.

Crash recovery (kill -9)
------------------------
The pool keeps no persistent or separately-stored counters; the counts are
pure functions of in-memory state. After a hard kill the process restarts
with an empty pool and rebuilds state from scratch, so there is nothing
stale to reconcile on the client side. Connections that were open at the
moment of the kill are cleaned up by the OS (sockets are closed when the
process dies, sending FIN/RST to the peer) and, on the server side, by the
server's own idle/session timeout. If an external system must agree on a
count (e.g. a server-side `max_connections`), the correct recovery is to
re-derive it from the authoritative source (actual open sessions) at
startup rather than trusting any persisted number.
"""

import collections
import threading
import time


class ConnectionClosedError(Exception):
    """Raised when an operation is attempted on a closed connection."""


class PoolError(Exception):
    """Raised on pool protocol violations (e.g. double give_back)."""


class ConnectionPool:
    def __init__(self, factory, idle_timeout=30.0, max_idle=10,
                 clock=time.monotonic, validator=None):
        self._factory = factory
        self._idle_timeout = idle_timeout
        self._max_idle = max_idle
        self._clock = clock
        # A connection is usable only if the peer has not closed it.
        self._validator = validator or (lambda c: not getattr(c, "closed", False))
        self._lock = threading.Lock()
        self._reap_lock = threading.Lock()   # guards against reaper re-entry
        self._idle = collections.deque()     # (conn, idle_since)
        self._borrowed = set()
        self._closed_count = 0
        self._stop_event = threading.Event()

    # -- borrower API -----------------------------------------------------

    def borrow(self):
        """Return a live connection, either recycled or newly created."""
        with self._lock:
            while self._idle:
                conn, _ = self._idle.pop()
                if self._validator(conn):
                    self._borrowed.add(conn)
                    return conn
                # Peer already closed it: drop and fix the count.
                self._closed_count += 1
        # Create outside the lock; the new conn is invisible to the reaper
        # until it is registered as borrowed.
        conn = self._factory()
        with self._lock:
            self._borrowed.add(conn)
        return conn

    def give_back(self, conn):
        """Return a borrowed connection to the pool.

        Raises PoolError if the connection was not borrowed from this pool
        (covers double-return), keeping the counters consistent.
        """
        close_it = False
        with self._lock:
            if conn not in self._borrowed:
                raise PoolError(
                    "connection was not borrowed from this pool "
                    "(already returned or foreign)")
            self._borrowed.remove(conn)
            if self._validator(conn) and len(self._idle) < self._max_idle:
                # Idle time starts NOW, not at borrow time.
                self._idle.append((conn, self._clock()))
            else:
                # Over max_idle, or dead connection: retire it.
                self._closed_count += 1
                close_it = True
        if close_it:
            conn.close()

    # -- reaper -----------------------------------------------------------

    def reap_once(self):
        """Close truly idle, expired (or dead) connections.

        Re-entrant/overlapping invocations are harmless: the second one is
        a no-op and returns 0. Only connections that are in `_idle` at scan
        time are eligible, and each victim is detached from `_idle` under
        the lock before being closed, so a borrowed connection can never
        be a victim.
        """
        if not self._reap_lock.acquire(blocking=False):
            return 0
        try:
            now = self._clock()
            with self._lock:
                victims = []
                survivors = collections.deque()
                while self._idle:
                    conn, idle_since = self._idle.popleft()
                    expired = now - idle_since > self._idle_timeout
                    if expired or not self._validator(conn):
                        victims.append(conn)
                        self._closed_count += 1
                    else:
                        survivors.append((conn, idle_since))
                self._idle = survivors
            # Close outside the lock. Victims are no longer reachable by
            # borrowers, so this cannot close an in-use connection.
            for conn in victims:
                conn.close()
            return len(victims)
        finally:
            self._reap_lock.release()

    def start_reaper(self, interval=1.0):
        """Start a background thread that reaps every `interval` seconds."""
        def loop():
            while not self._stop_event.wait(interval):
                self.reap_once()

        thread = threading.Thread(target=loop, daemon=True,
                                  name="pool-idle-reaper")
        thread.start()
        return thread

    def stop_reaper(self):
        self._stop_event.set()

    # -- introspection ----------------------------------------------------

    def stats(self):
        """Consistent snapshot; every number is derived under the lock."""
        with self._lock:
            idle = len(self._idle)
            borrowed = len(self._borrowed)
            return {
                "idle": idle,
                "borrowed": borrowed,
                "total": idle + borrowed,
                "closed": self._closed_count,
            }
