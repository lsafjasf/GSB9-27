"""Simulated primary/replica database cluster (stdlib only).

Primary exposes an LSN-ordered change log; replicas apply it asynchronously
with a configurable replication lag and support failure injection.
Each key carries a version (the LSN of the write that last modified it),
which the router uses for per-key monotonic-read tracking.
"""
import threading
import time


class ReplicaDownError(Exception):
    """Raised when reading from a replica that has been killed."""


class Primary:
    def __init__(self, query_delay=0.0):
        self._lock = threading.Lock()
        self._data = {}         # key -> (value, version)
        self._lsn = 0
        self._log = []          # list[(lsn, commit_ts, key, value)]
        self._commit_ts = {}    # lsn -> commit_ts
        self.query_delay = query_delay

    def write(self, key, value):
        with self._lock:
            self._lsn += 1
            ts = time.monotonic()
            self._data[key] = (value, self._lsn)
            self._log.append((self._lsn, ts, key, value))
            self._commit_ts[self._lsn] = ts
            return self._lsn

    def read(self, key):
        """Return (value, key_version, node_lsn)."""
        if self.query_delay:
            time.sleep(self.query_delay)
        with self._lock:
            value, version = self._data.get(key, (None, 0))
            return value, version, self._lsn

    def current_lsn(self):
        with self._lock:
            return self._lsn

    def log_since(self, lsn):
        with self._lock:
            return [e for e in self._log if e[0] > lsn]

    def commit_ts(self, lsn):
        with self._lock:
            return self._commit_ts.get(lsn)


class Replica:
    """Asynchronously applies the primary's log `lag` seconds behind."""

    def __init__(self, primary, lag, name="replica", query_delay=0.0):
        self.primary = primary
        self.lag = lag
        self.name = name
        self.query_delay = query_delay
        self._lock = threading.Lock()
        self._data = {}         # key -> (value, version)
        self._applied_lsn = 0
        self._alive = True
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._replicate, name=name, daemon=True)
        self._thread.start()

    # -- replication loop -------------------------------------------------
    def _replicate(self):
        while not self._stop.is_set():
            entries = self.primary.log_since(self.applied_lsn())
            if not entries:
                self._stop.wait(0.001)
                continue
            for lsn, ts, key, value in entries:
                delay = ts + self.lag - time.monotonic()
                if delay > 0 and self._stop.wait(delay):
                    return
                with self._lock:
                    if lsn > self._applied_lsn:
                        self._data[key] = (value, lsn)
                        self._applied_lsn = lsn

    # -- introspection ----------------------------------------------------
    def applied_lsn(self):
        with self._lock:
            return self._applied_lsn

    def estimated_lag(self):
        """Seconds the oldest unapplied primary entry has been waiting."""
        nxt = self.primary.commit_ts(self.applied_lsn() + 1)
        if nxt is None:
            return 0.0
        return max(0.0, time.monotonic() - nxt)

    def is_alive(self):
        return self._alive

    # -- operations -------------------------------------------------------
    def read(self, key):
        """Return (value, key_version, node_lsn)."""
        if not self._alive:
            raise ReplicaDownError(self.name)
        if self.query_delay:
            time.sleep(self.query_delay)
        with self._lock:
            value, version = self._data.get(key, (None, 0))
            return value, version, self._applied_lsn

    def kill(self):
        """Simulate a replica failure."""
        self._alive = False

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)
