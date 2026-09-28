"""AFTER the fix: TTL key-value store where every read path filters expiry.

Design:
  * A single gate (`_expired_locked`) decides liveness.  Point lookups, batch
    gets, range scans, aggregates and every iterator must pass through it.
  * Observed expiry is made sticky (`dead` flag): once an entry has been seen
    expired, rolling the clock backwards cannot resurrect it.  Explicit
    renewal (put / touch) clears the flag.
  * Reads never delete physical entries; only purge_expired / the Janitor do.
    Correctness therefore does not depend on the cleanup task having run.
  * ttl=None means "persist forever".
"""

import threading

from clock import Clock, SystemClock


class Entry:
    __slots__ = ("value", "expire_at", "dead")

    def __init__(self, value, expire_at):
        self.value = value
        self.expire_at = expire_at
        self.dead = False


_MISSING = object()


class TTLStore:
    def __init__(self, clock: Clock | None = None):
        self._clock = clock or SystemClock()
        self._data = {}
        self._lock = threading.RLock()

    # ---------- the one expiry gate used by every read path ----------

    def _expired_locked(self, entry: Entry, now: float) -> bool:
        if entry.expire_at is None:
            return False
        if now >= entry.expire_at:
            entry.dead = True
        return entry.dead

    # ---------- write paths ----------

    def put(self, key, value, ttl=None):
        expire_at = None if ttl is None else self._clock.now() + ttl
        with self._lock:
            self._data[key] = Entry(value, expire_at)

    def touch(self, key, ttl):
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False
            entry.expire_at = self._clock.now() + ttl
            entry.dead = False  # explicit renewal resurrects
            return True

    def delete(self, key):
        with self._lock:
            return self._data.pop(key, None) is not None

    # ---------- read path: point lookups ----------

    def get(self, key, default=None):
        now = self._clock.now()
        with self._lock:
            entry = self._data.get(key)
            if entry is None or self._expired_locked(entry, now):
                return default
            return entry.value

    def get_many(self, keys):
        now = self._clock.now()
        out = []
        with self._lock:
            for key in keys:
                entry = self._data.get(key)
                out.append(None if entry is None or self._expired_locked(entry, now)
                           else entry.value)
        return out

    def __contains__(self, key):
        now = self._clock.now()
        with self._lock:
            entry = self._data.get(key)
            return entry is not None and not self._expired_locked(entry, now)

    def ttl_of(self, key):
        now = self._clock.now()
        with self._lock:
            entry = self._data.get(key)
            if entry is None or self._expired_locked(entry, now):
                return None
            return None if entry.expire_at is None else entry.expire_at - now

    # ---------- read path: range scan ----------

    @staticmethod
    def _in_range(key, start, end, prefix):
        if start is not None and key < start:
            return False
        if end is not None and not key < end:
            return False
        if prefix is not None and not str(key).startswith(prefix):
            return False
        return True

    def scan(self, start=None, end=None, prefix=None):
        with self._lock:
            keys = list(self._data)
        for key in sorted(keys):
            if not self._in_range(key, start, end, prefix):
                continue
            now = self._clock.now()
            with self._lock:
                entry = self._data.get(key)
                if entry is None or self._expired_locked(entry, now):
                    continue
                yield key, entry.value

    # ---------- read path: iterators / collection views ----------

    def keys(self):
        for key, _ in self.scan():
            yield key

    def values(self):
        for _, value in self.scan():
            yield value

    def items(self):
        yield from self.scan()

    def __iter__(self):
        return self.keys()

    def __len__(self):
        return self.count()

    # ---------- read path: aggregates (all built on scan) ----------

    def count(self, start=None, end=None, prefix=None):
        return sum(1 for _ in self.scan(start, end, prefix))

    def sum(self, field, start=None, end=None, prefix=None):
        return sum(v[field] for _, v in self.scan(start, end, prefix))

    def avg(self, field, start=None, end=None, prefix=None):
        values = [v[field] for _, v in self.scan(start, end, prefix)]
        return sum(values) / len(values) if values else None

    def min(self, field, start=None, end=None, prefix=None):
        return min((v[field] for _, v in self.scan(start, end, prefix)), default=None)

    def max(self, field, start=None, end=None, prefix=None):
        return max((v[field] for _, v in self.scan(start, end, prefix)), default=None)

    # ---------- physical cleanup, decoupled from reads ----------

    def purge_expired(self):
        now = self._clock.now()
        with self._lock:
            dead = [k for k, e in self._data.items() if self._expired_locked(e, now)]
            for key in dead:
                del self._data[key]
            return len(dead)

    def physical_size(self):
        with self._lock:
            return len(self._data)


class ExpiryJanitor:
    """Background physical cleanup. Never required for read correctness."""

    def __init__(self, store: TTLStore, interval: float = 1.0):
        self._store = store
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def run_once(self):
        return self._store.purge_expired()

    def _loop(self):
        while not self._stop.wait(self._interval):
            self._store.purge_expired()

    def stop(self):
        self._stop.set()
        self._thread.join()
