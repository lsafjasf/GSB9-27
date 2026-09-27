"""修复前的朴素内存池（用于复现碎片问题）。

问题设计（故意保留的缺陷）：
- 空闲块用普通 list 存放，释放时直接 append，从不与相邻空闲块合并；
- 不做尺寸分档，按申请字节数精确切分，产生大量奇数尺寸的碎屑；
- first-fit 查找，且找到后把余量插到链表尾部，加速小块堆积。

结果：运行一段时间后总空闲内存很多，但最大连续空闲块很小，
大块分配失败 —— 即“总空闲很多却分配不出连续块”。
"""

from __future__ import annotations

import threading


class OutOfMemoryError(Exception):
    """池内已没有足够大的连续空闲块。"""


class Block:
    __slots__ = ("pool", "offset", "size", "_freed")

    def __init__(self, pool: "NaivePool", offset: int, size: int) -> None:
        self.pool = pool
        self.offset = offset
        self.size = size
        self._freed = False

    def view(self) -> memoryview:
        return memoryview(self.pool._buffer)[self.offset : self.offset + self.size]

    def free(self) -> None:
        self.pool.free(self)


class PoolStats:
    def __init__(self, total_free: int, max_block: int, free_blocks: int) -> None:
        self.total_free = total_free
        self.max_block = max_block
        self.free_blocks = free_blocks

    @property
    def fragmentation(self) -> float:
        """碎片率 = 最大可用连续块 / 总空闲。1.0 表示无碎片。"""
        if self.total_free == 0:
            return 1.0
        return self.max_block / self.total_free

    def __repr__(self) -> str:
        return (
            f"PoolStats(total_free={self.total_free}, max_block={self.max_block}, "
            f"free_blocks={self.free_blocks}, frag={self.fragmentation:.4f})"
        )


class NaivePool:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._buffer = bytearray(capacity)
        self._free: list[tuple[int, int]] = [(0, capacity)]  # (offset, size)，从不合并
        self._live: dict[int, int] = {}
        self._lock = threading.Lock()

    def alloc(self, size: int) -> Block:
        if size <= 0:
            raise ValueError("size must be positive")
        with self._lock:
            for i, (off, sz) in enumerate(self._free):
                if sz >= size:
                    self._free.pop(i)
                    if sz > size:
                        # 余量丢到链表尾部，不合并 —— 碎片源头之一
                        self._free.append((off + size, sz - size))
                    self._live[off] = size
                    return Block(self, off, size)
        raise OutOfMemoryError(
            f"alloc({size}) failed: no contiguous free block large enough"
        )

    def free(self, block: Block) -> None:
        with self._lock:
            if block._freed:
                raise RuntimeError("double free")
            block._freed = True
            del self._live[block.offset]
            # 直接挂回链表，从不与左右空闲邻居合并 —— 碎片源头之二
            self._free.append((block.offset, block.size))

    def stats(self) -> PoolStats:
        with self._lock:
            total = sum(sz for _, sz in self._free)
            max_block = max((sz for _, sz in self._free), default=0)
            return PoolStats(total, max_block, len(self._free))
