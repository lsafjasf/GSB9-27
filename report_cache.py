"""Version-based report cache with single-flight recomputation.

Only Python standard library is used. Time is injectable via ``clock``
callables so tests can run deterministically.

Core ideas
----------
* Every data domain has a monotonically increasing version in
  ``DataRegistry``. A report declares which domains it depends on.
* A cache entry stores the dependency-version snapshot taken when it was
  built. On read, the snapshot is compared with the current versions:
  mismatch means *this entry only* is stale -- nothing is flushed globally.
* Concurrent misses for the same report key are coalesced (single-flight):
  one thread recomputes, the others wait and share the result.
* If recomputation fails, the previous value is kept and served
  (stale fallback) and the error is counted in metrics.
"""

import threading
import time
from collections import defaultdict


class DataRegistry:
    """Tracks versions and last-change timestamps of data domains."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._versions = defaultdict(int)
        self._changed_at = {}

    def bump(self, name):
        """Mark a data domain as changed (e.g. after a write)."""
        with self._lock:
            self._versions[name] += 1
            self._changed_at[name] = self._clock()

    def version(self, name):
        with self._lock:
            return self._versions[name]

    def changed_at(self, name):
        """Timestamp of the last change, or None if never changed."""
        with self._lock:
            return self._changed_at.get(name)

    def snapshot(self, names):
        with self._lock:
            return {n: self._versions[n] for n in names}


class _Entry:
    __slots__ = ("value", "versions", "built_at")

    def __init__(self, value, versions, built_at):
        self.value = value
        self.versions = versions
        self.built_at = built_at


class _Flight:
    """In-flight recomputation shared by concurrent waiters."""

    __slots__ = ("event", "error")

    def __init__(self):
        self.event = threading.Event()
        self.error = None


class ReportCache:
    """Caches report results keyed by (report_id, key)."""

    def __init__(self, registry, clock=time.monotonic):
        self._registry = registry
        self._clock = clock
        self._reports = {}
        self._entries = {}
        self._inflight = {}
        self._lock = threading.Lock()
        # metrics
        self.hits = 0
        self.misses = 0
        self.recomputes = 0
        self.errors = 0
        self.stale_serves = 0
        self._staleness_samples = []

    def register(self, report_id, dependencies, compute):
        """Register a report.

        ``dependencies``: iterable of data-domain names the report reads.
        ``compute``: zero-arg callable producing the report value.
        """
        self._reports[report_id] = (tuple(dependencies), compute)

    def get(self, report_id, key=()):
        deps, compute = self._reports[report_id]
        ckey = (report_id, key)
        current = self._registry.snapshot(deps)

        with self._lock:
            entry = self._entries.get(ckey)
            if entry is not None and entry.versions == current:
                self.hits += 1
                return entry.value
            self.misses += 1
            flight = self._inflight.get(ckey)
            if flight is None:
                flight = _Flight()
                self._inflight[ckey] = flight
                leader = True
            else:
                leader = False

        if not leader:
            flight.event.wait()
            with self._lock:
                entry = self._entries.get(ckey)
            if entry is not None:
                return entry.value
            # Leader failed and there was no previous value: re-raise.
            raise flight.error

        # Leader path: do the single recomputation.
        try:
            value = compute()
        except Exception as exc:
            with self._lock:
                self.errors += 1
                self._inflight.pop(ckey, None)
                entry = self._entries.get(ckey)
                if entry is not None:
                    self.stale_serves += 1
                flight.error = exc
                flight.event.set()
            if entry is not None:
                return entry.value  # keep serving the old value
            raise

        now = self._clock()
        with self._lock:
            self.recomputes += 1
            old = self._entries.get(ckey)
            if old is not None:
                changed = [d for d in deps
                           if old.versions.get(d) != current.get(d)]
                self._record_staleness(changed, now)
            self._entries[ckey] = _Entry(value, current, now)
            self._inflight.pop(ckey, None)
            flight.event.set()
        return value

    def _record_staleness(self, changed_deps, now):
        """Staleness of a refresh = age of the oldest un-reflected change."""
        ages = []
        for d in changed_deps:
            changed_at = self._registry.changed_at(d)
            if changed_at is not None:
                ages.append(now - changed_at)
        if ages:
            self._staleness_samples.append(max(ages))

    def metrics(self):
        with self._lock:
            total = self.hits + self.misses
            avg_staleness = (sum(self._staleness_samples)
                             / len(self._staleness_samples)
                             if self._staleness_samples else 0.0)
            return {
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": self.hits / total if total else 0.0,
                "recomputes": self.recomputes,
                "errors": self.errors,
                "stale_serves": self.stale_serves,
                "avg_staleness": avg_staleness,
                "staleness_samples": len(self._staleness_samples),
            }

    def reset_metrics(self):
        with self._lock:
            self.hits = self.misses = self.recomputes = 0
            self.errors = self.stale_serves = 0
            self._staleness_samples.clear()
