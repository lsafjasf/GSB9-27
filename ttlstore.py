"""Fixed TTL KV store.

Design:
- Expiration is lazy: sweep() only reclaims memory and is fully decoupled
  from reads. Correctness never depends on sweep() having run.
- Every read path (get / scan / count / items) funnels through a single
  visibility predicate evaluated under the lock with one consistent
  timestamp, so no path can bypass the expiry check.
- The clock is injectable. A monotonic guard (max of observed readings)
  ensures a wall-clock rollback can never resurrect an expired, unrenewed
  entry.
- ttl <= 0 means "already expired at write time"; the entry is stored
  but is never visible on any read path.
"""

import threading
import time


class TTLStore:
    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._data = {}  # key -> (value, expires_at or None)
        self._last_now = float("-inf")

    def _now(self):
        with self._lock:
            t = self._clock()
            if t > self._last_now:
                self._last_now = t
            return self._last_now

    @staticmethod
    def _expired(expires_at, now):
        return expires_at is not None and expires_at <= now

    def _visible_items_locked(self, now):
        return [
            (k, v)
            for k, (v, exp) in self._data.items()
            if not self._expired(exp, now)
        ]

    def put(self, key, value, ttl=None):
        expires_at = None if ttl is None else self._now() + ttl
        with self._lock:
            self._data[key] = (value, expires_at)

    def touch(self, key, ttl):
        """Explicit renewal. Fails on missing or already-expired keys."""
        with self._lock:
            now = self._now()
            entry = self._data.get(key)
            if entry is None or self._expired(entry[1], now):
                return False
            self._data[key] = (entry[0], now + ttl)
            return True

    def get(self, key):
        with self._lock:
            now = self._now()
            entry = self._data.get(key)
            if entry is None or self._expired(entry[1], now):
                return None
            return entry[0]

    def scan(self, start=None, end=None):
        with self._lock:
            now = self._now()
            return sorted(
                (k, v)
                for k, v in self._visible_items_locked(now)
                if (start is None or k >= start) and (end is None or k < end)
            )

    def count(self):
        with self._lock:
            now = self._now()
            return len(self._visible_items_locked(now))

    def items(self):
        # Snapshot under the lock with a single timestamp: the iterator is
        # consistent and never yields expired entries, even if the clock
        # advances while the caller is consuming it.
        with self._lock:
            now = self._now()
            snapshot = self._visible_items_locked(now)
        return iter(snapshot)

    def delete(self, key):
        with self._lock:
            self._data.pop(key, None)

    def sweep(self):
        """Physical cleanup only. Reads are correct whether or not this runs."""
        with self._lock:
            now = self._now()
            dead = [k for k, (_, exp) in self._data.items() if self._expired(exp, now)]
            for k in dead:
                del self._data[k]
            return len(dead)

    def raw_size(self):
        with self._lock:
            return len(self._data)
