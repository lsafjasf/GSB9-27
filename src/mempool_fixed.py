"""修复后的内存池。

修复手段（组合）：
1. 尺寸分档：申请向上取整到 2 的幂（最小 16B），把碎片粒度约束在有限档位，
   消除任意奇数尺寸造成的碎屑；
2. 合并相邻空闲块：空闲链表按偏移量有序，释放时与左右相邻空闲块立即合并
   （boundary-tag 思路的简化实现），保证空闲链表始终是“极大化”的；
3. best-fit：在满足条件的空闲块中选最小的切分，把大块留给大对象。

并发语义：
- 所有内部状态由一把互斥锁保护；
- “整理”只合并*空闲*块，从不移动或释放已分配块 —— 已分配块的
  offset/size/内容在其整个生命周期内不变（由并发回归测试断言）。
"""

from __future__ import annotations

import bisect
import threading

MIN_CLASS_SHIFT = 4  # 最小档 16B


class OutOfMemoryError(Exception):
    """池内已没有足够大的连续空闲块。"""


def class_size(n: int) -> int:
    """把申请尺寸向上取整到 2 的幂档位（最小 16B）。"""
    if n <= 0:
        raise ValueError("size must be positive")
    s = 1 << MIN_CLASS_SHIFT
    while s < n:
        s <<= 1
    return s


class Block:
    __slots__ = ("pool", "offset", "size", "_freed")

    def __init__(self, pool: "MemoryPool", offset: int, size: int) -> None:
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


class MemoryPool:
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._buffer = bytearray(capacity)
        # 不变量（持锁期间恒成立）：
        #   I1 _free 按 offset 严格升序；
        #   I2 _free 中任意两块互不重叠且互不相邻（相邻即应已合并）；
        #   I3 _free 与 _live 互不重叠，二者并集恰好覆盖 [0, capacity)；
        #   I4 已分配块的 (offset, size) 在 free() 之前绝不改变。
        self._free: list[tuple[int, int]] = [(0, capacity)]
        self._live: dict[int, int] = {}  # offset -> size
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ alloc
    def alloc(self, size: int) -> Block:
        need = class_size(size)
        with self._lock:
            best_idx = -1
            best_sz = None
            for i, (off, sz) in enumerate(self._free):
                if sz >= need and (best_sz is None or sz < best_sz):
                    best_idx, best_sz = i, sz
                    if sz == need:  # 完美匹配，提前结束
                        break
            if best_idx < 0:
                raise OutOfMemoryError(
                    f"alloc({size}) failed: no contiguous free block >= {need}"
                )
            off, sz = self._free.pop(best_idx)
            if sz > need:
                self._free.insert(best_idx, (off + need, sz - need))
            self._live[off] = need
            return Block(self, off, need)

    # ------------------------------------------------------------------- free
    def free(self, block: Block) -> None:
        with self._lock:
            if block._freed:
                raise RuntimeError("double free")
            block._freed = True
            del self._live[block.offset]
            self._insert_and_coalesce(block.offset, block.size)

    def _insert_and_coalesce(self, off: int, sz: int) -> None:
        """按偏移量有序插入，并与左右相邻空闲块合并。只动空闲块。"""
        i = bisect.bisect_left(self._free, (off, 0))
        # 与左邻居合并
        if i > 0:
            po, ps = self._free[i - 1]
            if po + ps == off:
                off, sz = po, ps + sz
                i -= 1
                self._free.pop(i)
        # 与右邻居（可能多个）合并
        while i < len(self._free) and self._free[i][0] == off + sz:
            sz += self._free[i][1]
            self._free.pop(i)
        self._free.insert(i, (off, sz))

    # ------------------------------------------------------------------ stats
    def stats(self) -> PoolStats:
        with self._lock:
            total = sum(sz for _, sz in self._free)
            max_block = max((sz for _, sz in self._free), default=0)
            return PoolStats(total, max_block, len(self._free))

    # ------------------------------------------------------------- invariants
    def check_invariants(self) -> None:
        """断言池的内部不变量；并发测试在运行期间反复调用。"""
        with self._lock:
            prev_end = -1
            free_total = 0
            for off, sz in self._free:
                assert sz > 0, "empty free block"
                assert off >= 0 and off + sz <= self.capacity, "free block out of range"
                assert off > prev_end, (
                    f"free list not sorted/coalesced at offset {off} "
                    f"(prev_end={prev_end})"
                )
                prev_end = off + sz
                free_total += sz
            live_total = 0
            for off, sz in self._live.items():
                assert sz > 0 and off >= 0 and off + sz <= self.capacity
                live_total += sz
            # 空闲与存活块互不重叠
            live_sorted = sorted(self._live.items())
            for i in range(1, len(live_sorted)):
                assert live_sorted[i - 1][0] + live_sorted[i - 1][1] <= live_sorted[i][0], (
                    "live blocks overlap"
                )
            assert free_total + live_total == self.capacity, (
                f"accounting mismatch: free={free_total} live={live_total} "
                f"capacity={self.capacity}"
            )
