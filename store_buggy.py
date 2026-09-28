"""BEFORE the fix: TTL key-value store with lazy expiry only on get().

Read paths such as get_many / scan / aggregates / iteration never run the
expiry check, so expired data stays visible through them.  Writing an already
expired entry stores it as live, and an observed expiry is undone when the
clock rolls backwards.  Physical cleanup (purge_expired) is coupled to reads
because reads are the only place where deletion happens.
"""

from clock import Clock, SystemClock


class Entry:
    __slots__ = ("value", "expire_at")

    def __init__(self, value, expire_at):
        self.value = value
        self.expire_at = expire_at


class TTLStore:
    def __init__(self, clock: Clock | None = None):
        self._clock = clock or SystemClock()
        self._data = {}

    def put(self, key, value, ttl):
        expire_at = self._clock.now() + ttl
        self._data[key] = Entry(value, expire_at)

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None:
            return default
        if self._clock.now() >= entry.expire_at:
            del self._data[key]
            return default
        return entry.value

    def get_many(self, keys):
        return [self._data[k].value if k in self._data else None for k in keys]

    def __contains__(self, key):
        return key in self._data

    def ttl_of(self, key):
        entry = self._data.get(key)
        if entry is None:
            return None
        return entry.expire_at - self._clock.now()

    def touch(self, key, ttl):
        entry = self._data.get(key)
        if entry is not None:
            entry.expire_at = self._clock.now() + ttl
            return True
        return False

    def delete(self, key):
        return self._data.pop(key, None) is not None

    def scan(self, start=None, end=None, prefix=None):
        for key in sorted(self._data):
            if start is not None and key < start:
                continue
            if end is not None and not key < end:
                continue
            if prefix is not None and not str(key).startswith(prefix):
                continue
            yield key, self._data[key].value

    def keys(self):
        return list(self._data.keys())

    def values(self):
        return [e.value for e in self._data.values()]

    def items(self):
        return list(self._data.items())

    def __iter__(self):
        return iter(list(self._data))

    def __len__(self):
        return len(self._data)

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

    def purge_expired(self):
        now = self._clock.now()
        dead = [k for k, e in self._data.items() if now >= e.expire_at]
        for key in dead:
            del self._data[key]
        return len(dead)

    def physical_size(self):
        return len(self._data)
