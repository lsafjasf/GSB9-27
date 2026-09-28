"""Optimistic concurrency control (OCC) for an in-memory key-value store.

Standard library only. Python 3.9+.

Design notes
------------
Read set / write set recording:
  * read set    : {key: version observed at read time}
  * range reads : [(lo, hi, frozenset of live keys observed in [lo, hi))]
  * write set   : {key: new value, or _MISSING for delete} (buffered, private)

Validation (at commit, under the commit lock):
  * every key in the read set must still have the recorded version;
  * every recorded range is re-scanned: the live key set must be identical
    (this is what rules out phantoms);
  * blind writes (written but never read) never conflict - last committer wins.

Fairness:
  * conflicts are retried with full-jitter exponential backoff, capped;
  * after ``senior_after`` failed attempts the transaction enters a global
    FIFO "senior queue".  While that queue is non-empty, ordinary commits
    block; seniors re-execute and commit one at a time, in arrival order,
    against a quiesced store, so a senior commit cannot conflict.
    See README.md for the waiting-time bound.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque

__all__ = [
    "OCCStore",
    "Transaction",
    "ConflictError",
    "PhantomConflict",
    "RetryExhausted",
    "Stats",
]

_MISSING = object()  # tombstone marker stored as a value


class ConflictError(Exception):
    """Validation failed: a key in the read set was modified by another txn."""


class PhantomConflict(ConflictError):
    """Validation failed: a scanned range would return a different key set."""


class RetryExhausted(Exception):
    """The transaction conflicted more than ``max_retries`` times."""

    def __init__(self, attempts: int):
        self.attempts = attempts
        super().__init__(
            f"transaction aborted: {attempts} conflict(s), retry limit reached"
        )


class Stats:
    """Thread-safe counters, exposed for tests and benchmarks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.commits = 0
        self.conflicts = 0
        self.phantoms = 0
        self.retries = 0
        self.senior_commits = 0

    def bump(self, name: str, n: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + n)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "commits": self.commits,
                "conflicts": self.conflicts,
                "phantoms": self.phantoms,
                "retries": self.retries,
                "senior_commits": self.senior_commits,
            }


class Transaction:
    """One optimistic attempt.  Created by :meth:`OCCStore.run`; do not reuse."""

    def __init__(self, store: "OCCStore") -> None:
        self._store = store
        self.read_set: dict = {}        # key -> version observed
        self.range_reads: list = []     # [(lo, hi, frozenset(live keys))]
        self.write_set: dict = {}       # key -> value or _MISSING (delete)

    def get(self, key, default=None):
        if key in self.write_set:  # read-your-writes
            value = self.write_set[key]
            return default if value is _MISSING else value
        value, version = self._store._data.get(key, (_MISSING, 0))
        if key not in self.read_set:
            self.read_set[key] = version
        return default if value is _MISSING else value

    def scan(self, lo, hi):
        """Return sorted [(key, value)] with lo <= key < hi.

        The set of *committed* live keys in the range is recorded and
        re-validated at commit time (phantom detection).
        """
        committed = {}
        for k, (v, _ver) in self._store._data.items():
            if lo <= k < hi and v is not _MISSING:
                committed[k] = v
        self.range_reads.append((lo, hi, frozenset(committed)))
        merged = dict(committed)
        for k, v in self.write_set.items():  # overlay own writes
            if lo <= k < hi:
                if v is _MISSING:
                    merged.pop(k, None)
                else:
                    merged[k] = v
        return sorted(merged.items())

    def put(self, key, value) -> None:
        self.write_set[key] = value

    def delete(self, key) -> None:
        self.write_set[key] = _MISSING

    def commit(self) -> None:
        """Validate and commit; raises ConflictError on validation failure."""
        self._store._commit(self)


class OCCStore:
    def __init__(self) -> None:
        # key -> (value, version).  Deleted keys keep a (_MISSING, version)
        # tombstone so versions are never reused.
        self._data: dict = {}
        self._clock = 0
        self._commit_cond = threading.Condition()
        self._senior_queue: deque = deque()
        self.stats = Stats()

    # ------------------------------------------------------------------ API

    def run(self, fn, *, max_retries: int = 8, senior_after: int = 3,
            base_backoff: float = 0.0005, max_backoff: float = 0.02):
        """Execute ``fn(txn)`` with OCC retries; return fn's result.

        Raises RetryExhausted after ``max_retries`` failed attempts.
        After ``senior_after`` failures the attempt is executed through the
        FIFO senior queue, where it is guaranteed to commit.
        """
        attempts = 0
        while True:
            txn = Transaction(self)
            result = fn(txn)
            try:
                self._commit(txn)
                return result
            except ConflictError:
                attempts += 1
                self.stats.bump("retries")
                if attempts > max_retries:
                    raise RetryExhausted(attempts)
                if attempts >= senior_after:
                    return self._run_senior(fn)
                cap = min(max_backoff, base_backoff * (2 ** attempts))
                time.sleep(random.uniform(0.0, cap))  # full-jitter backoff

    def get(self, key, default=None):
        return self.run(lambda tx: tx.get(key, default))

    def put(self, key, value) -> None:
        self.run(lambda tx: tx.put(key, value))

    # ------------------------------------------------------------- internals

    def _commit(self, txn: Transaction) -> None:
        with self._commit_cond:
            while self._senior_queue:  # seniors waiting: ordinary commits yield
                self._commit_cond.wait()
            self._validate(txn)
            self._apply(txn)
            self.stats.bump("commits")

    def _run_senior(self, fn):
        """FIFO fair path: re-execute fn with no concurrent committers."""
        token = object()
        with self._commit_cond:
            self._senior_queue.append(token)
        try:
            with self._commit_cond:
                while self._senior_queue[0] is not token:
                    self._commit_cond.wait()
            # Head of the queue: the store is quiesced (ordinary commits are
            # blocked while the queue is non-empty, other seniors wait their
            # turn), so validation below cannot fail.
            txn = Transaction(self)
            result = fn(txn)
            with self._commit_cond:
                self._senior_queue.popleft()
                try:
                    self._validate(txn)
                    self._apply(txn)
                    self.stats.bump("commits")
                    self.stats.bump("senior_commits")
                finally:
                    self._commit_cond.notify_all()
            return result
        except BaseException:
            with self._commit_cond:
                if token in self._senior_queue:
                    self._senior_queue.remove(token)
                self._commit_cond.notify_all()
            raise

    def _validate(self, txn: Transaction) -> None:
        for key, version in txn.read_set.items():
            _value, current = self._data.get(key, (_MISSING, 0))
            if current != version:
                self.stats.bump("conflicts")
                raise ConflictError(f"key {key!r} modified by another transaction")
        for lo, hi, keys in txn.range_reads:
            current = frozenset(
                k for k, (v, _ver) in self._data.items()
                if lo <= k < hi and v is not _MISSING
            )
            if current != keys:
                self.stats.bump("conflicts")
                self.stats.bump("phantoms")
                raise PhantomConflict(
                    f"range [{lo!r}, {hi!r}) changed: "
                    f"+{sorted(current - keys)} -{sorted(keys - current)}"
                )

    def _apply(self, txn: Transaction) -> None:
        if not txn.write_set:
            return
        self._clock += 1
        for key, value in txn.write_set.items():
            self._data[key] = (value, self._clock)
