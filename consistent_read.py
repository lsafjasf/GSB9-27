"""Session-level read consistency for primary/replica (read-write split) setups.

Stdlib only. Python 3.8+.

Guarantees implemented (session level):
  * Read-Your-Writes (RYW): a session always sees the effects of its own writes.
  * Monotonic Reads (MR):   a session never observes a state older than one it
                            has already seen.

Mechanism:
  * Every write on the primary returns a monotonically increasing LSN.
  * A session tracks `last_write_lsn` (RYW) and `last_read_lsn` (MR).
  * A read requires `required_lsn = max(last_write_lsn, last_read_lsn)` and is
    served by a replica only if that replica has applied up to `required_lsn`
    AND its estimated replication lag is within `lag_threshold` seconds.
  * Otherwise the read falls back to the primary.

A defensive violation counter verifies every served read against
`required_lsn`; with the guard enabled it must always stay zero.
"""

import threading
import time
from collections import deque


class ReplicaDown(Exception):
    """Raised when a read is attempted on a replica marked as down."""


class Primary:
    """Simulated primary database with a WAL (source of truth for LSNs)."""

    def __init__(self, read_latency=0.0):
        self._lock = threading.Lock()
        self._lsn = 0
        self._data = {}
        self._wal = deque()  # entries: (lsn, commit_ts, key, value)
        self.read_latency = read_latency  # optional simulated per-read cost (s)

    def write(self, key, value):
        """Apply a write, return its LSN."""
        with self._lock:
            self._lsn += 1
            self._data[key] = value
            self._wal.append((self._lsn, time.monotonic(), key, value))
            return self._lsn

    def read(self, key):
        """Strongly consistent read. Returns (value, current_lsn)."""
        if self.read_latency:
            time.sleep(self.read_latency)
        with self._lock:
            return self._data.get(key), self._lsn

    def current_lsn(self):
        with self._lock:
            return self._lsn

    def wal_since(self, lsn):
        with self._lock:
            return [e for e in self._wal if e[0] > lsn]

    def commit_ts(self, lsn):
        """Commit timestamp of a WAL entry, or None if unknown."""
        with self._lock:
            for entry in self._wal:
                if entry[0] == lsn:
                    return entry[1]
        return None


class Replica:
    """Simulated read replica that asynchronously applies the primary's WAL.

    `lag` is the configured replication delay: a WAL entry becomes visible
    only `lag` seconds after it committed on the primary.
    """

    def __init__(self, primary, lag=0.0, read_latency=None, poll_interval=0.001):
        self._primary = primary
        self.lag = lag
        self.read_latency = read_latency if read_latency is not None else primary.read_latency
        self.poll_interval = poll_interval
        self._lock = threading.Lock()
        self._data = {}
        self._applied_lsn = 0
        self._down = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._replicate_loop, daemon=True)
        self._thread.start()

    # -- replication ------------------------------------------------------
    def _replicate_loop(self):
        while not self._stop.is_set():
            self.replicate_once()
            time.sleep(self.poll_interval)

    def replicate_once(self):
        if self.is_down:
            return
        now = time.monotonic()
        for lsn, commit_ts, key, value in self._primary.wal_since(self.applied_lsn):
            if now - commit_ts >= self.lag:
                with self._lock:
                    if lsn > self._applied_lsn:
                        self._data[key] = value
                        self._applied_lsn = lsn
            else:
                break  # WAL is ordered; later entries are newer

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    # -- failure injection --------------------------------------------------
    @property
    def is_down(self):
        with self._lock:
            return self._down

    def set_down(self, down):
        with self._lock:
            self._down = down

    # -- state inspection ---------------------------------------------------
    @property
    def applied_lsn(self):
        with self._lock:
            return self._applied_lsn

    def estimated_lag(self):
        """Estimated replication lag in seconds.

        Defined as the age of the oldest WAL entry not yet applied.
        Returns 0.0 when fully caught up, +inf when down.
        """
        if self.is_down:
            return float("inf")
        applied = self.applied_lsn
        if applied >= self._primary.current_lsn():
            return 0.0
        ts = self._primary.commit_ts(applied + 1)
        if ts is None:
            return float("inf")
        return max(0.0, time.monotonic() - ts)

    # -- reads ----------------------------------------------------------------
    def read(self, key):
        """Read as of the replica's applied LSN. Returns (value, applied_lsn)."""
        if self.is_down:
            raise ReplicaDown("replica is down")
        if self.read_latency:
            time.sleep(self.read_latency)
        with self._lock:
            return self._data.get(key), self._applied_lsn


class Metrics:
    """Thread-safe counters for consistency verification and cost analysis."""

    def __init__(self):
        self._lock = threading.Lock()
        self.replica_reads = 0
        self.fallback_reads = 0
        self.violations = 0  # reads served with lsn < required_lsn (must be 0)

    def record(self, source, violated):
        with self._lock:
            if source == "replica":
                self.replica_reads += 1
            else:
                self.fallback_reads += 1
            if violated:
                self.violations += 1

    def snapshot(self):
        with self._lock:
            total = self.replica_reads + self.fallback_reads
            return {
                "total_reads": total,
                "replica_reads": self.replica_reads,
                "fallback_reads": self.fallback_reads,
                "fallback_ratio": (self.fallback_reads / total) if total else 0.0,
                "violations": self.violations,
            }


class Session:
    """Per-session state: the LSNs that back the RYW / MR guarantees."""

    def __init__(self, router):
        self._router = router
        self.last_write_lsn = 0  # RYW: newest LSN this session has written
        self.last_read_lsn = 0   # MR:  newest LSN this session has observed

    def write(self, key, value):
        lsn = self._router.primary.write(key, value)
        self.last_write_lsn = max(self.last_write_lsn, lsn)
        return lsn

    def read(self, key):
        return self._router.read(self, key)


class Router:
    """Routes session reads to a replica or falls back to the primary.

    guarded=True  -> enforce RYW + MR + lag threshold (violations stay 0)
    guarded=False -> naive "always read a replica" routing, used to measure
                     how many violations the guard actually prevents.
    """

    def __init__(self, primary, replicas, lag_threshold=0.05, guarded=True, metrics=None):
        self.primary = primary
        self.replicas = list(replicas)
        self.lag_threshold = lag_threshold
        self.guarded = guarded
        self.metrics = metrics if metrics is not None else Metrics()

    def session(self):
        return Session(self)

    def _pick_replica(self, required_lsn):
        best = None
        for replica in self.replicas:
            if replica.is_down:
                continue
            if self.guarded:
                if replica.applied_lsn < required_lsn:
                    continue  # cannot satisfy RYW / MR
                if replica.estimated_lag() > self.lag_threshold:
                    continue  # freshness policy: too far behind
            if best is None or replica.applied_lsn > best.applied_lsn:
                best = replica
        return best

    def read(self, session, key):
        required_lsn = max(session.last_write_lsn, session.last_read_lsn)
        replica = self._pick_replica(required_lsn)
        if replica is not None:
            try:
                value, lsn = replica.read(key)
                source = "replica"
            except ReplicaDown:  # raced with a failover: retry on primary
                value, lsn = self.primary.read(key)
                source = "primary"
        else:
            value, lsn = self.primary.read(key)
            source = "primary"

        violated = lsn < required_lsn
        self.metrics.record(source, violated)
        session.last_read_lsn = max(session.last_read_lsn, lsn)
        return value
