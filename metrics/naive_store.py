"""The original buggy implementation, kept for the reproduction script.

Bugs:
1. Aggregation buckets live only in memory; after a restart the current
   window starts from zero -> a mid-window restart produces a value that is
   only "after" instead of "before + after", and old windows vanish too.
2. Monotone series is rebuilt from in-memory buckets -> it goes backwards.
3. flush() writes the file in place (not atomic) and writes no checksum;
   a torn/truncated file is swallowed by bare except and the counters
   silently start again from zero.
"""

from __future__ import annotations

import json
import os
import time


class NaiveStore:
    def __init__(self, state_path, window_seconds=3600, clock=time.time):
        self.state_path = os.fspath(state_path)
        self.window_seconds = window_seconds
        self._clock = clock
        self.counters = {}
        self.windows = {}
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, encoding="utf-8") as handle:
                    self.counters = json.load(handle)["counters"]
            except Exception:
                # BUG: any corruption => silently start from zero
                self.counters = {}

    def _bucket(self, ts=None):
        if ts is None:
            ts = self._clock()
        return int(ts) // self.window_seconds

    def incr(self, name, amount=1, ts=None):
        self.counters[name] = self.counters.get(name, 0) + amount
        buckets = self.windows.setdefault(name, {})
        index = self._bucket(ts)
        buckets[index] = buckets.get(index, 0) + amount
        return self.counters[name]

    def value(self, name):
        return self.counters.get(name, 0)

    def window_value(self, name, ts=None):
        return self.windows.get(name, {}).get(self._bucket(ts), 0)

    def series(self, name, start_ts, end_ts=None):
        if end_ts is None:
            end_ts = self._clock()
        first, last = self._bucket(start_ts), self._bucket(end_ts)
        return [(i * self.window_seconds, self.windows.get(name, {}).get(i, 0))
                for i in range(first, last + 1)]

    def flush(self):
        # BUG: non-atomic in-place write, no fsync, no checksum
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump({"counters": self.counters}, handle)
