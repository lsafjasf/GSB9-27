"""滑动窗口极值结构（最小值 / 最大值）。

核心思想：单调双端队列（monotone deque）。
  - _min_q 保存下标，对应值从队首到队尾单调不降，队首即窗口最小值；
  - _max_q 保存下标，对应值从队首到队尾单调不升，队首即窗口最大值。

复杂度：
  - push:  均摊 O(1)。每个下标入队一次、出队至多一次（队尾被更优值弹出，
           或过期后从队首弹出），总操作数 O(n)，均摊到每次 push 为 O(1)。
  - min/max: O(1)。窗口非空时队首即答案。
  - resize: O(min(w, n))。窗口变更后按新窗口对历史数据重建两条单调队列，
           调用方无需重建结构；resize 通常是低频操作。
  - 内存:  O(n)。为支持"窗口调大后历史数据重新生效"，全部历史值被保留。

边界行为约定：
  - 窗口大小为 0，或尚无任何元素：min()/max() 返回 None；
  - 窗口大于已入队数量：覆盖全部已入队元素；
  - 元素相等：单调队列用 >= / <= 弹栈，同值时保留下标更大（更晚过期）者，
    结果与暴力一致；
  - 窗口大小为 1：极值即最近一次插入的元素。
"""

from collections import deque

__all__ = ["SlidingWindowExtrema"]


class SlidingWindowExtrema:
    __slots__ = ("_w", "_n", "_values", "_min_q", "_max_q")

    def __init__(self, window: int):
        self._validate_window(window)
        self._w = window          # 当前窗口大小
        self._n = 0               # 累计插入次数（元素的全局下标 = n-1）
        self._values = []         # 全部历史值，按下标索引
        self._min_q = deque()     # 下标递增，对应值单调不降
        self._max_q = deque()     # 下标递增，对应值单调不升

    @staticmethod
    def _validate_window(window):
        if isinstance(window, bool) or not isinstance(window, int) or window < 0:
            raise ValueError("window must be a non-negative integer")

    @property
    def window(self) -> int:
        return self._w

    def __len__(self) -> int:
        """当前窗口实际覆盖的元素个数。"""
        return min(self._w, self._n)

    def push(self, value) -> None:
        idx = self._n
        self._values.append(value)
        self._n += 1

        min_q = self._min_q
        while min_q and self._values[min_q[-1]] >= value:
            min_q.pop()
        min_q.append(idx)

        max_q = self._max_q
        while max_q and self._values[max_q[-1]] <= value:
            max_q.pop()
        max_q.append(idx)

        self._evict_expired()

    def _evict_expired(self) -> None:
        """弹出下标落在当前窗口 [n-w, n) 之外的队首元素。"""
        lo = self._n - self._w
        min_q = self._min_q
        while min_q and min_q[0] < lo:
            min_q.popleft()
        max_q = self._max_q
        while max_q and max_q[0] < lo:
            max_q.popleft()

    def min(self):
        """当前窗口最小值；窗口为空（w==0 或无元素）时返回 None。"""
        if len(self) == 0:
            return None
        return self._values[self._min_q[0]]

    def max(self):
        """当前窗口最大值；窗口为空（w==0 或无元素）时返回 None。"""
        if len(self) == 0:
            return None
        return self._values[self._max_q[0]]

    def resize(self, new_window: int) -> None:
        """动态调整窗口大小，已有历史数据按新窗口重新生效。

        窗口调大时，此前落在窗口外、但仍保留在历史中的元素会重新参与
        极值计算；调用方无需重建结构。复杂度 O(min(new_window, n))。
        """
        self._validate_window(new_window)
        self._w = new_window
        lo = max(0, self._n - new_window)

        min_q = self._min_q
        max_q = self._max_q
        min_q.clear()
        max_q.clear()
        values = self._values
        for i in range(lo, self._n):
            v = values[i]
            while min_q and values[min_q[-1]] >= v:
                min_q.pop()
            min_q.append(i)
            while max_q and values[max_q[-1]] <= v:
                max_q.pop()
            max_q.append(i)
