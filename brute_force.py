"""Obvious O(window) reference implementation used for differential tests."""

from typing import Any, List, Optional, Tuple


class BruteForceWindowMinMax:
    """Recomputes min/max over the active window on every single query."""

    def __init__(self, window_size: int) -> None:
        if window_size < 0:
            raise ValueError("window_size must be >= 0")
        self._window_size = window_size
        self._values: List[Any] = []

    @property
    def window_size(self) -> int:
        return self._window_size

    @property
    def count(self) -> int:
        return len(self._values)

    @property
    def active_count(self) -> int:
        return min(len(self._values), self._window_size)

    def push(self, value: Any) -> None:
        self._values.append(value)

    def resize(self, new_size: int) -> None:
        if new_size < 0:
            raise ValueError("new_size must be >= 0")
        self._window_size = new_size

    def _active(self) -> List[Any]:
        if self._window_size == 0:
            return []
        return self._values[-self._window_size:]

    def get_min(self) -> Optional[Any]:
        active = self._active()
        return min(active) if active else None

    def get_max(self) -> Optional[Any]:
        active = self._active()
        return max(active) if active else None

    def get_min_max(self) -> Tuple[Optional[Any], Optional[Any]]:
        return self.get_min(), self.get_max()
