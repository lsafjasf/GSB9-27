"""暴力参照实现：每次查询直接对窗口切片求 min/max，用于对拍与耗时对比。"""


class BruteForceExtrema:
    def __init__(self, window: int):
        if isinstance(window, bool) or not isinstance(window, int) or window < 0:
            raise ValueError("window must be a non-negative integer")
        self._w = window
        self._data = []

    @property
    def window(self) -> int:
        return self._w

    def __len__(self) -> int:
        return min(self._w, len(self._data))

    def push(self, value) -> None:
        self._data.append(value)

    def _window_view(self):
        k = min(self._w, len(self._data))
        if k == 0:
            return []
        return self._data[len(self._data) - k:]

    def min(self):
        view = self._window_view()
        return min(view) if view else None

    def max(self):
        view = self._window_view()
        return max(view) if view else None

    def resize(self, new_window: int) -> None:
        if isinstance(new_window, bool) or not isinstance(new_window, int) or new_window < 0:
            raise ValueError("window must be a non-negative integer")
        self._w = new_window
