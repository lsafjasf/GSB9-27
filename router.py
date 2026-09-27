"""Session-level consistency routing for read/write splitting (stdlib only).

Guarantees provided by ConsistentRouter:
  * Read-your-writes (RYW): a session never reads a state older than its
    own last write (global write watermark).
  * Monotonic reads: a session never sees an older version of a key than
    one it has already observed (per-key version watermark).

Fallback policy: a replica is eligible only if it is alive, has applied up
to the session's required LSN, and its estimated replication lag is within
`lag_threshold`. Otherwise the router optionally waits up to
`catchup_timeout` for a replica to catch up, then falls back to the primary.
"""
import random
import threading
import time

from db_cluster import ReplicaDownError


class Session:
    """Per-session causality tracker."""

    def __init__(self):
        self.last_write_lsn = 0        # RYW watermark (global)
        self.last_read_versions = {}   # key -> highest version observed

    def required_lsn(self, key):
        return max(self.last_write_lsn,
                   self.last_read_versions.get(key, 0))


class Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self.primary_reads = 0
        self.replica_reads = 0
        self.fallbacks = 0        # reads that fell back to the primary
        self.catchup_waits = 0    # reads served after waiting for catch-up
        self.violations = 0       # consistency violations observed
        self.total_read_latency = 0.0

    def record(self, **kw):
        with self._lock:
            for key, delta in kw.items():
                setattr(self, key, getattr(self, key) + delta)

    @property
    def total_reads(self):
        return self.primary_reads + self.replica_reads

    @property
    def fallback_ratio(self):
        return self.fallbacks / self.total_reads if self.total_reads else 0.0

    @property
    def avg_read_latency(self):
        return (self.total_read_latency / self.total_reads
                if self.total_reads else 0.0)

    def snapshot(self):
        with self._lock:
            return dict(primary_reads=self.primary_reads,
                        replica_reads=self.replica_reads,
                        fallbacks=self.fallbacks,
                        catchup_waits=self.catchup_waits,
                        violations=self.violations,
                        total_reads=self.total_reads,
                        fallback_ratio=self.fallback_ratio,
                        avg_read_latency=self.avg_read_latency)


class ConsistentRouter:
    def __init__(self, primary, replicas, lag_threshold=float("inf"),
                 catchup_timeout=0.0, poll_interval=0.001):
        self.primary = primary
        self.replicas = list(replicas)
        self.lag_threshold = lag_threshold
        self.catchup_timeout = catchup_timeout
        self.poll_interval = poll_interval
        self.stats = Stats()

    def session(self):
        return Session()

    # -- writes always go to the primary ----------------------------------
    def write(self, session, key, value):
        lsn = self.primary.write(key, value)
        session.last_write_lsn = max(session.last_write_lsn, lsn)
        return lsn

    # -- consistent reads ---------------------------------------------------
    def read(self, session, key):
        required = session.required_lsn(key)
        start = time.monotonic()

        replica = self._pick_replica(required)
        waited = False
        if replica is None and self.catchup_timeout > 0:
            deadline = start + self.catchup_timeout
            while time.monotonic() < deadline:
                time.sleep(self.poll_interval)
                replica = self._pick_replica(required)
                if replica is not None:
                    waited = True
                    break

        if replica is not None:
            try:
                value, version, node_lsn = replica.read(key)
            except ReplicaDownError:
                replica = None  # raced with a failure; fall through

        if replica is None:
            value, version, node_lsn = self.primary.read(key)
            self.stats.record(primary_reads=1, fallbacks=1)
        else:
            self.stats.record(replica_reads=1,
                              catchup_waits=1 if waited else 0)

        # Advance the per-key monotonic watermark with what we observed.
        prev = session.last_read_versions.get(key, 0)
        session.last_read_versions[key] = max(prev, version)

        # Verify the guarantee held (this is what the tests assert on).
        if node_lsn < required:
            self.stats.record(violations=1)

        self.stats.record(total_read_latency=time.monotonic() - start)
        return value

    def _pick_replica(self, required_lsn):
        candidates = [
            r for r in self.replicas
            if r.is_alive()
            and r.applied_lsn() >= required_lsn
            and r.estimated_lag() <= self.lag_threshold
        ]
        return random.choice(candidates) if candidates else None


class NaiveRouter(ConsistentRouter):
    """Baseline: random alive replica, no LSN/lag checks (for comparison)."""

    def _pick_replica(self, required_lsn):
        candidates = [r for r in self.replicas if r.is_alive()]
        return random.choice(candidates) if candidates else None
