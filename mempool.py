"""Fixed memory pool.

Fix strategy (combination of the suggested approaches):
  1. Alignment-based size classes: every request is rounded up to a
     multiple of ALIGNMENT, so freed blocks recombine cleanly instead of
     producing odd-sized slivers.
  2. Best-fit allocation with splitting, so large free blocks are not
     needlessly consumed by small requests.
  3. Eager coalescing of adjacent free blocks on every free(), using
     offset-indexed boundary tags (O(1) neighbour lookup).

Concurrency: all structural mutations happen under one lock.  Coalescing
only ever touches *free* blocks -- allocated blocks are never moved,
resized or released during compaction, so an offset handed out by alloc()
stays valid and stable until the owner calls free().

The pool never falls back to the system allocator per-request: it manages
one fixed arena acquired at construction time.
"""

import bisect
import threading

ALIGNMENT = 16


class OutOfMemoryError(Exception):
    pass


def _align(size):
    return (size + ALIGNMENT - 1) // ALIGNMENT * ALIGNMENT


class MemoryPool:
    def __init__(self, capacity):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.arena = bytearray(capacity)
        self._lock = threading.Lock()
        self._free_by_offset = {0: capacity}  # start offset -> size
        self._free_by_end = {capacity: 0}     # end offset   -> start offset
        self._by_size = {capacity: {0}}       # size -> set of start offsets
        self._sizes = [capacity]              # sorted distinct free sizes
        self._allocated = {}                  # offset -> aligned size

    # ---- internal helpers; caller must hold self._lock ----

    def _insert_free_block(self, offset, size):
        self._free_by_offset[offset] = size
        self._free_by_end[offset + size] = offset
        bucket = self._by_size.get(size)
        if bucket is None:
            bucket = self._by_size[size] = set()
            bisect.insort(self._sizes, size)
        bucket.add(offset)

    def _remove_free_block(self, offset):
        size = self._free_by_offset.pop(offset)
        del self._free_by_end[offset + size]
        bucket = self._by_size[size]
        bucket.discard(offset)
        if not bucket:
            del self._by_size[size]
            i = bisect.bisect_left(self._sizes, size)
            self._sizes.pop(i)
        return size

    # ---- public API (thread-safe) ----

    def alloc(self, size):
        if size <= 0:
            raise ValueError("size must be positive")
        need = _align(size)
        with self._lock:
            i = bisect.bisect_left(self._sizes, need)
            if i == len(self._sizes):
                # NB: build the message from raw state -- self._lock is
                # already held and is not reentrant.
                total = sum(self._free_by_offset.values())
                largest = self._sizes[-1] if self._sizes else 0
                raise OutOfMemoryError(
                    "cannot allocate %d bytes: total free=%d, "
                    "largest free block=%d"
                    % (size, total, largest)
                )
            bsz = self._sizes[i]
            offset = next(iter(self._by_size[bsz]))
            self._remove_free_block(offset)
            remainder = bsz - need
            if remainder >= ALIGNMENT:
                self._insert_free_block(offset + need, remainder)
                self._allocated[offset] = need
            else:
                # remainder too small to track: hand out the whole block
                self._allocated[offset] = bsz
            return offset

    def free(self, offset):
        with self._lock:
            if offset not in self._allocated:
                raise ValueError("invalid or double free at offset %d" % offset)
            size = self._allocated.pop(offset)
            # Coalesce with the next block (starts exactly where we end).
            next_size = self._free_by_offset.get(offset + size)
            if next_size is not None:
                self._remove_free_block(offset + size)
                size += next_size
            # Coalesce with the previous block (ends exactly where we start).
            prev_start = self._free_by_end.get(offset)
            if prev_start is not None:
                prev_size = self._remove_free_block(prev_start)
                offset = prev_start
                size += prev_size
            self._insert_free_block(offset, size)

    # ---- metrics ----

    def total_free(self):
        with self._lock:
            return sum(self._free_by_offset.values())

    def largest_free_block(self):
        with self._lock:
            return self._sizes[-1] if self._sizes else 0

    def fragmentation(self):
        """1 - largest_free / total_free; 0 = perfect, close to 1 = bad."""
        with self._lock:
            total = sum(self._free_by_offset.values())
            if total == 0:
                return 0.0
            return 1.0 - (self._sizes[-1] if self._sizes else 0) / total

    # ---- structural invariants (used by tests / debugging) ----

    def check_invariants(self):
        """Validate pool structure.  Raises AssertionError on violation.

        Invariants:
          I1 free blocks are pairwise disjoint and sorted by offset.
          I2 free blocks are fully coalesced (no two are adjacent).
          I3 allocated blocks are pairwise disjoint.
          I4 free and allocated blocks do not overlap.
          I5 free + allocated exactly tiles [0, capacity).
        """
        with self._lock:
            free_blocks = sorted(self._free_by_offset.items())
            alloc_blocks = sorted(self._allocated.items())

            for (off, sz), (noff, _) in zip(free_blocks, free_blocks[1:]):
                assert off + sz <= noff, "I1: overlapping free blocks"
                assert off + sz < noff, "I2: adjacent free blocks not coalesced"
            for (off, sz), (noff, _) in zip(alloc_blocks, alloc_blocks[1:]):
                assert off + sz <= noff, "I3: overlapping allocations"

            intervals = sorted(free_blocks + alloc_blocks)
            cursor = 0
            for off, sz in intervals:
                assert off == cursor, "I4/I5: gap or overlap at %d" % off
                assert sz > 0, "I5: zero-sized block"
                cursor = off + sz
            assert cursor == self.capacity, "I5: arena not fully accounted"

            for off, sz in free_blocks:
                assert self._free_by_end[off + sz] == off
                assert off in self._by_size[sz]
            assert sorted(self._by_size) == self._sizes
            return True
