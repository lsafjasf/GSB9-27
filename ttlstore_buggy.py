"""Buggy TTL KV store (pre-fix baseline, kept only for repro/comparison).

Bug: expiration is lazy (physical removal happens only in sweep()), and
only get() performs the expiry check. scan(), count() and items() read
raw slots directly, so expired-but-not-yet-swept entries leak to readers.
"""

import threading
import time


class BuggyTTLStore:
    def __init__(self, clock=None):
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._data = {}  # key -> (value, expires_at or None)

    def _now(self):
        return self._clock()

    @staticmethod
    def _expired(expires_at, now):
        return expires_at is not None and expires_at <= now

    def put(self, key, value, ttl=None):
        expires_at = None if ttl is None else self._now() + ttl
        with self._lock:
            self._data[key] = (value, expires_at)

    def touch(self, key, ttl):
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False
            self._data[key] = (entry[0], self._now() + ttl)
            return True

    def get(self, key):
        with self._lock:
            entry = self._data.get(key)
            if entry is None or self._expired(entry[1], self._now()):
                return None
            return entry[0]

    def scan(self, start=None, end=None):
        # BUG: no expiry check.
        with self._lock:
            return sorted(
                (k, v)
                for k, (v, _exp) in self._data.items()
                if (start is None or k >= start) and (end is None or k < end)
            )

    def count(self):
        # BUG: no expiry check.
        with self._lock:
            return len(self._data)

    def items(self):
        # BUG: no expiry check.
        with self._lock:
            snapshot = list(self._data.items())
        for k, (v, _exp) in snapshot:
            yield k, v

    def delete(self, key):
        with self._lock:
            self._data.pop(key, None)

    def sweep(self):
        with self._lock:
            now = self._now()
            dead = [k for k, (_, exp) in self._data.items() if self._expired(exp, now)]
            for k in dead:
                del self._data[k]
            return len(dead)

    def raw_size(self):
        with self._lock:
            return len(self._data)
