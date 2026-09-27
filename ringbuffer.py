"""Overwrite ring buffer: single-writer / multi-reader, lock-free (seqlock).

Concurrency & memory-ordering strategy
--------------------------------------
No global mutex serializes readers against the writer. Instead we use a
per-slot sequence lock (seqlock) plus a monotonic global write sequence:

* Each slot ``i`` carries a published sequence number ``_slot_seq[i]``.
* The writer performs, in order:
    1. ``seq = _write_seq + 1``
    2. store record into ``_slots[idx]``          (data store)
    3. store ``seq`` into ``_slot_seq[idx]``      (release: publishes data)
    4. store ``seq`` into ``_write_seq``          (release: advances head)
* A reader performs, in order:
    1. load ``_write_seq``                        (acquire: overrun check)
    2. load ``s1 = _slot_seq[idx]``               (acquire)
    3. load record from ``_slots[idx]``
    4. load ``s2 = _slot_seq[idx]``               (re-check)
    5. accept only if ``s1 == s2 == expected_seq``; otherwise the slot was
       overwritten mid-read -> re-run the overrun check and retry.

Overrun detection: a reader tracks ``next_seq`` (the sequence it expects to
read next). Before reading it compares against ``_write_seq``: if
``_write_seq - next_seq >= capacity`` the records
``[next_seq, _write_seq - capacity]`` are gone forever and ``OverrunError``
reports that exact lost range. A record can therefore never be silently
misaligned: either the slot still holds exactly ``next_seq`` (verified by the
seqlock) or the reader is told precisely which sequences were lost.

Memory barriers: under CPython the GIL gives every bytecode atomic execution
and a total store order, so the ordered stores above act as release stores
and the reader's loads as acquire loads; the seqlock re-check (step 4) closes
the race where the writer ran entirely between the reader's loads. No
``threading.Lock`` is taken on either path. On an interpreter without the
GIL (e.g. free-threaded Python) the same protocol is correct provided the
sequence stores/loads are done with release/acquire semantics (e.g. via
``atomic`` operations); the algorithm itself does not change.
"""

from __future__ import annotations

import threading


class OverrunError(Exception):
    """Raised when the reader fell behind and records were overwritten.

    Attributes:
        lost_from: first lost sequence number (inclusive).
        lost_to:   last lost sequence number (inclusive).
    The reader automatically skips past the lost range; the next ``read()``
    returns the oldest record still present.
    """

    def __init__(self, lost_from: int, lost_to: int):
        self.lost_from = lost_from
        self.lost_to = lost_to
        super().__init__(
            f"overrun: lost sequences [{lost_from}, {lost_to}] "
            f"({lost_to - lost_from + 1} records)"
        )


class RingBuffer:
    """Fixed-capacity overwrite ring buffer. Single writer, many readers."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._slots: list = [None] * capacity
        self._slot_seq: list[int] = [0] * capacity
        self._write_seq = 0  # last published sequence; sequences start at 1

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def write_seq(self) -> int:
        """Sequence number of the most recently written record."""
        return self._write_seq

    def write(self, record) -> int:
        """Append ``record``, overwriting the oldest record if full.

        Writer-side only; must be called from a single thread (or otherwise
        externally serialized). Returns the assigned sequence number.
        """
        seq = self._write_seq + 1
        idx = (seq - 1) % self._capacity
        self._slots[idx] = record        # 1. data store
        self._slot_seq[idx] = seq        # 2. release: publish slot
        self._write_seq = seq            # 3. release: advance head
        return seq

    def reader(self) -> "Reader":
        """Create an independent reader starting at the oldest live record."""
        return Reader(self)


class Reader:
    """Independent read cursor over a RingBuffer. Not thread-safe itself."""

    def __init__(self, buf: RingBuffer):
        self._buf = buf
        self._next = buf._write_seq - buf._capacity + 1
        if self._next < 1:
            self._next = 1

    @property
    def next_seq(self) -> int:
        return self._next

    def read(self):
        """Return ``(seq, record)`` for the next record, or ``None`` if empty.

        Raises OverrunError if records were overwritten before being read;
        the cursor is then positioned at the oldest surviving record.
        """
        buf = self._buf
        capacity = buf._capacity
        while True:
            write_seq = buf._write_seq              # acquire
            if self._next > write_seq:
                return None                         # nothing new
            if write_seq - self._next >= capacity:
                lost_from = self._next
                lost_to = write_seq - capacity
                self._next = lost_to + 1
                raise OverrunError(lost_from, lost_to)
            idx = (self._next - 1) % capacity
            s1 = buf._slot_seq[idx]                 # acquire
            record = buf._slots[idx]
            s2 = buf._slot_seq[idx]                 # re-check
            if s1 == s2 == self._next:
                self._next += 1
                return s1, record
            # Slot overwritten (or being overwritten) during the read:
            # loop to re-evaluate overrun / retry. Never return torn data.
