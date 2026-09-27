"""稀疏表（Sparse Table）区间最值查询（RMQ）。

- 预处理：O(n log n) 时间，O(n log n) 空间（约 n*(log2(n)+1) 个槽位）。
- 查询：O(1) 时间回答任意闭区间 [l, r] 的最值与最值下标。
- 平局规则（确定性）：区间内出现多个相同最值时，返回**最小下标**（最左出现位置）。
- 仅使用 Python 标准库。
"""

from math import log2


class SparseTable:
    """静态数组的区间最值查询结构。数组构建后不可修改。"""

    __slots__ = ("_n", "_log", "_min_st", "_max_st", "_arr")

    def __init__(self, arr):
        n = len(arr)
        if n == 0:
            raise ValueError("SparseTable 不支持空数组")
        self._n = n
        # log[i] = floor(log2(i))，预计算使查询 O(1)
        self._log = [0] * (n + 1)
        for i in range(2, n + 1):
            self._log[i] = self._log[i >> 1] + 1

        levels = self._log[n] + 1
        # 每层存放下标：st[k][i] 为区间 [i, i+2^k) 的最值下标（平局取最左）
        self._min_st = [list(range(n))]
        self._max_st = [list(range(n))]
        k = 1
        while k < levels:
            half = 1 << (k - 1)
            length = n - (1 << k) + 1
            prev_min, prev_max = self._min_st[-1], self._max_st[-1]
            row_min = [0] * length
            row_max = [0] * length
            for i in range(length):
                a, b = prev_min[i], prev_min[i + half]
                # 严格小于才取右半，保证平局时保留最左下标
                row_min[i] = b if arr[b] < arr[a] else a
                c, d = prev_max[i], prev_max[i + half]
                row_max[i] = d if arr[d] > arr[c] else c
            self._min_st.append(row_min)
            self._max_st.append(row_max)
            k += 1
        self._arr = arr  # 保留引用用于取值（不复制）

    def __len__(self):
        return self._n

    def _check(self, l, r):
        if not (0 <= l <= r < self._n):
            raise IndexError(f"非法区间 [{l}, {r}]，数组长度 {self._n}")

    def min_index(self, l, r):
        """返回 [l, r] 最小值的下标；多个最小值时返回最左下标。O(1)。"""
        self._check(l, r)
        k = self._log[r - l + 1]
        row = self._min_st[k]
        a, b = row[l], row[r - (1 << k) + 1]
        return b if self._arr[b] < self._arr[a] else a

    def max_index(self, l, r):
        """返回 [l, r] 最大值的下标；多个最大值时返回最左下标。O(1)。"""
        self._check(l, r)
        k = self._log[r - l + 1]
        row = self._max_st[k]
        a, b = row[l], row[r - (1 << k) + 1]
        return b if self._arr[b] > self._arr[a] else a

    def range_min(self, l, r):
        """返回 (最小值, 最左下标)。"""
        i = self.min_index(l, r)
        return self._arr[i], i

    def range_max(self, l, r):
        """返回 (最大值, 最左下标)。"""
        i = self.max_index(l, r)
        return self._arr[i], i

    def memory_bytes(self):
        """估算结构自身占用的内存（不含原数组）。"""
        import sys
        # 每行列表的指针数组（sys.getsizeof 已含）；高层行的元素是
        # 第 0 行 int 对象的引用，不重复占内存，故 int 对象只计第 0 行。
        int_size = sys.getsizeof(self._n)
        total = sys.getsizeof(self._log) + len(self._log) * int_size
        for st in (self._min_st, self._max_st):
            total += sum(sys.getsizeof(row) for row in st)
            total += len(st[0]) * int_size
        return total
