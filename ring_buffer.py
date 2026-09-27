"""Single-writer / multi-reader overwrite ring buffer (Python 3, stdlib only).

Design notes
------------
Concurrency model: one writer thread, any number of reader threads.
There is NO lock on the data path. Coordination is done entirely with
monotonically increasing sequence numbers (Disruptor-style):

* Every record gets a global sequence number ``s`` (0, 1, 2, ...).
* Record ``s`` lives in slot ``s % capacity``.
* Each slot carries a *published sequence* ``slot_seq[slot]`` stating which
  global sequence the slot currently holds.

Writer protocol (program order matters):
    1. store payload into ``slots[slot]``
    2. store ``s`` into ``slot_seq[slot]``      # release-publish
    3. advance ``next_seq`` to ``s + 1``        # high-water mark, stored last

Reader protocol:
    1. read high-water mark ``hi = next_seq - 1`` and low-water mark
       ``lo = max(0, next_seq - capacity)``
    2. if its cursor < lo, records ``[cursor, lo - 1]`` were overwritten:
       report the lost range and jump the cursor to ``lo``
    3. load-acquire ``slot_seq[slot]``; only if it equals the wanted ``seq``
       read the payload, then re-check ``slot_seq[slot]`` -- if it changed,
       the slot was overwritten mid-read, so retry (never return torn data)

Memory-barrier rationale (CPython):
    CPython executes bytecodes under the GIL and never reorders Python-level
    stores as observed by other threads, so ordinary ordered stores/loads
    give the release/acquire semantics the protocol needs: a reader that
    observes ``slot_seq[slot] == s`` is guaranteed to observe the payload
    store that happened before it, and ``next_seq`` is only advanced after
    the slot is fully published. In a language without these guarantees
    (C/C++/Java) you would use store-release for step 2/3 and load-acquire
    for the matching reads; the algorithm itself is unchanged.

The only lock in the module is a ``threading.Condition`` used purely as a
sleep/wake mechanism for *blocking* readers; it is never held while record
data is written or read, so reads and writes are never serialized by it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Generic, List, Optional, Tuple, TypeVar

__all__ = ["OverwriteRingBuffer", "Reader", "ReadResult"]

T = TypeVar("T")


@dataclass(frozen=True)
class ReadResult(Generic[T]):
    """Outcome of one successful read.

    ``lost`` is ``(first_lost_seq, last_lost_seq)`` (inclusive) when the
    reader was overrun before this record; records in that range were
    overwritten and are gone forever. ``None`` means nothing was lost
    since the previous read.
    """

    seq: int
    item: T
    lost: Optional[Tuple[int, int]] = None


class OverwriteRingBuffer(Generic[T]):
    """Fixed-capacity ring buffer; writes overwrite the oldest record."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._slots: List[Optional[T]] = [None] * capacity
        self._slot_seq: List[int] = [-1] * capacity
        self._next_seq = 0  # writer-owned; readers only read it
        # Sleep/wake only; never serializes the data path.
        self._not_empty = threading.Condition()
        self._waiters = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    # ------------------------------------------------------------------ #
    # writer side (single thread)
    # ------------------------------------------------------------------ #
    def write(self, item: T) -> int:
        """Append ``item``, overwriting the oldest record if full.

        Returns the global sequence number assigned to the record.
        """
        seq = self._next_seq
        slot = seq % self._capacity
        self._slots[slot] = item          # 1. payload first
        self._slot_seq[slot] = seq        # 2. release-publish the slot
        self._next_seq = seq + 1          # 3. advance high-water mark last
        if self._waiters:                 # wake blocking readers (rare path)
            with self._not_empty:
                self._not_empty.notify_all()
        return seq

    # ------------------------------------------------------------------ #
    # shared water marks (atomic int loads under CPython)
    # ------------------------------------------------------------------ #
    def latest_seq(self) -> int:
        """Highest published sequence, or -1 when empty."""
        return self._next_seq - 1

    def oldest_seq(self) -> int:
        """Lowest sequence still available; older ones were overwritten."""
        return max(0, self._next_seq - self._capacity)

    def __len__(self) -> int:
        return min(self._next_seq, self._capacity)

    def reader(self, start: Optional[int] = None) -> "Reader[T]":
        """Create an independent reader.

        ``start`` is the first sequence the reader wants; default is the
        oldest sequence still available in the buffer.
        """
        return Reader(self, self.oldest_seq() if start is None else start)


class Reader(Generic[T]):
    """Independent read cursor over an :class:`OverwriteRingBuffer`."""

    def __init__(self, buf: OverwriteRingBuffer[T], start: int):
        if start < 0:
            raise ValueError("start must be >= 0")
        self._buf = buf
        self._cursor = start

    @property
    def cursor(self) -> int:
        """Next sequence this reader will try to read."""
        return self._cursor

    def poll(self) -> Optional[ReadResult[T]]:
        """Non-blocking read. Returns ``None`` when no new record exists.

        Never returns torn or misaligned data: if the slot is overwritten
        concurrently, the read is retried with fresh water marks.
        """
        buf = self._buf
        capacity = buf._capacity
        while True:
            if self._cursor > buf.latest_seq():
                return None
            lo = buf.oldest_seq()
            lost: Optional[Tuple[int, int]] = None
            if self._cursor < lo:  # overrun: report the gap, then jump
                lost = (self._cursor, lo - 1)
                self._cursor = lo
            seq = self._cursor
            slot = seq % capacity
            if buf._slot_seq[slot] != seq:      # acquire: slot not ours (raced)
                continue
            item = buf._slots[slot]             # read payload
            if buf._slot_seq[slot] != seq:      # validate: overwritten mid-read
                continue
            self._cursor = seq + 1
            return ReadResult(seq=seq, item=item, lost=lost)  # type: ignore[arg-type]

    def read(self, timeout: Optional[float] = None) -> ReadResult[T]:
        """Blocking read; raises ``TimeoutError`` if ``timeout`` elapses."""
        result = self.poll()
        if result is not None:
            return result
        deadline = None if timeout is None else time.monotonic() + timeout
        buf = self._buf
        while True:
            with buf._not_empty:
                buf._waiters += 1
                try:
                    result = self.poll()  # re-check under the condition
                    if result is not None:
                        return result
                    if deadline is None:
                        buf._not_empty.wait()
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("no record within timeout")
                        buf._not_empty.wait(remaining)
                finally:
                    buf._waiters -= 1
