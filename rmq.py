"""区间最值查询（RMQ）—— 稀疏表（Sparse Table）实现，仅依赖标准库。

复杂度（n = len(data)）：
  - 预处理：时间 O(n log n)，空间 O(n log n)
  - 查询：  时间 O(1)，返回 (最值, 下标)

平局规则（确定性）：
  - range_min：区间内多个相同最小值时，返回最靠左（下标最小）的那个。
  - range_max：区间内多个相同最大值时，返回最靠左（下标最小）的那个。

区间约定：range_min(l, r) / range_max(l, r) 均为闭区间 [l, r]，0 <= l <= r < n。
"""

from array import array


class SparseTableRMQ:
    __slots__ = ("data", "n", "log", "_min_idx", "_max_idx")

    def __init__(self, data):
        n = len(data)
        if n == 0:
            raise ValueError("data must be non-empty")
        self.data = data
        self.n = n

        # log[i] = floor(log2(i))，用于查询时取区间长度对应的层
        self.log = [0] * (n + 1)
        for i in range(2, n + 1):
            self.log[i] = self.log[i >> 1] + 1

        # 每层存“该起点、长度 2^j 的区间”的最优下标（uint32，4 字节/项）
        self._min_idx = [array("I", range(n))]
        self._max_idx = [array("I", range(n))]
        j = 1
        while (1 << j) <= n:
            half = 1 << (j - 1)
            length = n - (1 << j) + 1
            prev_min = self._min_idx[-1]
            prev_max = self._max_idx[-1]
            # 平局时 a 是左半区间的下标，必然 <= b，故 <= / >= 取等号即实现“取最左”
            row_min = array("I", [
                a if data[a] <= data[b] else b
                for a, b in zip(prev_min, prev_min[half:half + length])
            ])
            row_max = array("I", [
                a if data[a] >= data[b] else b
                for a, b in zip(prev_max, prev_max[half:half + length])
            ])
            self._min_idx.append(row_min)
            self._max_idx.append(row_max)
            j += 1

    def _check(self, l, r):
        if not (0 <= l <= r < self.n):
            raise IndexError(f"invalid range [{l}, {r}] for n={self.n}")

    def range_min(self, l, r):
        """返回闭区间 [l, r] 的 (最小值, 下标)；相同最小值取最左下标。"""
        self._check(l, r)
        k = self.log[r - l + 1]
        row = self._min_idx[k]
        a = row[l]
        b = row[r - (1 << k) + 1]
        da = self.data[a]
        db = self.data[b]
        i = a if da < db or (da == db and a < b) else b
        return self.data[i], i

    def range_max(self, l, r):
        """返回闭区间 [l, r] 的 (最大值, 下标)；相同最大值取最左下标。"""
        self._check(l, r)
        k = self.log[r - l + 1]
        row = self._max_idx[k]
        a = row[l]
        b = row[r - (1 << k) + 1]
        da = self.data[a]
        db = self.data[b]
        i = a if da > db or (da == db and a < b) else b
        return self.data[i], i

    def memory_bytes(self):
        """稀疏表本体（不含原始 data）占用的字节数，用于内存统计。"""
        import sys
        total = sys.getsizeof(self.log) + sum(
            sys.getsizeof(x) for x in self.log
        )
        for table in (self._min_idx, self._max_idx):
            for row in table:
                total += sys.getsizeof(row)
        return total
