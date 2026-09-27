"""Flawed memory pool: first-fit free list that splits but NEVER coalesces.

This is the original implementation that exhibits the production issue:
over a long-running service the free list fills up with small, non-adjacent
fragments.  Total free memory stays high, but no single free block is large
enough for a big allocation, so allocation fails spuriously.
"""


class OutOfMemoryError(Exception):
    pass


class BuggyMemoryPool:
    def __init__(self, capacity):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.arena = bytearray(capacity)
        self._free = [(0, capacity)]  # list of (offset, size), unsorted
        self._allocated = {}          # offset -> size

    def alloc(self, size):
        if size <= 0:
            raise ValueError("size must be positive")
        for i, (off, bsz) in enumerate(self._free):
            if bsz >= size:
                del self._free[i]
                if bsz > size:
                    # split, but the remainder is just appended; freed blocks
                    # are never merged back with their neighbours.
                    self._free.append((off + size, bsz - size))
                self._allocated[off] = size
                return off
        raise OutOfMemoryError(
            "cannot allocate %d bytes: total free=%d, largest free block=%d"
            % (size, self.total_free(), self.largest_free_block())
        )

    def free(self, offset):
        if offset not in self._allocated:
            raise ValueError("invalid or double free at offset %d" % offset)
        size = self._allocated.pop(offset)
        self._free.append((offset, size))  # BUG: no coalescing

    def total_free(self):
        return sum(sz for _, sz in self._free)

    def largest_free_block(self):
        return max((sz for _, sz in self._free), default=0)

    def fragmentation(self):
        """1 - largest_free / total_free; 0 = perfect, close to 1 = bad."""
        total = self.total_free()
        if total == 0:
            return 0.0
        return 1.0 - self.largest_free_block() / total
