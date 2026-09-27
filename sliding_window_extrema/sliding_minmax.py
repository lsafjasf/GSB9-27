"""Streaming sliding-window minimum/maximum with O(1) amortized updates.

Data structure
--------------
Two monotonic deques over insertion indices (0-based, strictly increasing):

* ``min_deque``: referenced values are non-decreasing front -> back.
* ``max_deque``: referenced values are non-increasing front -> back.

On every insertion, indices at the back whose value is *greater-or-equal*
(min deque) / *less-or-equal* (max deque) than the new value are discarded:
they can never be an extremum while the new element stays in the window.
Indices that leave the window are dropped from the front lazily.
The front index therefore always points at the current extremum.

Equal elements intentionally pop the old index ("==" pop rule). This is
required so that, when the window grows later, a stale duplicate that is now
outside the window cannot hide the fresh equal copy behind it.

Dynamic window resize
---------------------
All inserted raw values are retained, so a window change never asks the
caller to rebuild the structure:

* shrinking only expires extra indices from the deque fronts (O(window));
* growing rebuilds both deques by replaying the (at most) ``new_size``
  newest raw values (O(new_size));
* resize to 0 simply empties both deques.

Complexity (n = total inserts ever, w = current window size)
------------------------------------------------------------
push     : O(1) amortized (each index enters and leaves each deque once)
query    : O(1) amortized (lazy front expiry; no re-scan)
resize   : O(w) worst case (amortized O(1) per "recovered" element on grow)
memory   : O(n) raw history + O(w) deque bookkeeping

A window size of 0 is legal: pushes are recorded but queries report an
empty window.
"""

from collections import deque
from typing import Any, Optional, Tuple


class SlidingWindowMinMax:
    """Maintain the min and max of the last ``window_size`` pushed values."""

    def __init__(self, window_size: int) -> None:
        if not isinstance(window_size, int) or isinstance(window_size, bool):
            raise TypeError("window_size must be an int")
        if window_size < 0:
            raise ValueError("window_size must be >= 0")
        self._window_size = window_size
        self._values = []          # raw history, index == insertion order
        self._min_deque = deque()  # indices, values non-decreasing
        self._max_deque = deque()  # indices, values non-increasing

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #
    @property
    def window_size(self) -> int:
        """Configured capacity of the sliding window."""
        return self._window_size

    @property
    def count(self) -> int:
        """Total number of values pushed since construction."""
        return len(self._values)

    @property
    def active_count(self) -> int:
        """Number of values currently inside the window (<= window_size)."""
        return min(len(self._values), self._window_size)

    # ------------------------------------------------------------------ #
    # Core operations
    # ------------------------------------------------------------------ #
    def push(self, value: Any) -> None:
        """Insert one value (values must be mutually orderable)."""
        idx = len(self._values)
        self._values.append(value)

        if self._window_size == 0:
            # Nothing is in effect; history is still kept for a later resize.
            return

        min_dq = self._min_deque
        while min_dq and self._values[min_dq[-1]] >= value:
            min_dq.pop()
        min_dq.append(idx)

        max_dq = self._max_deque
        while max_dq and self._values[max_dq[-1]] <= value:
            max_dq.pop()
        max_dq.append(idx)

        self._expire(idx - self._window_size)

    def resize(self, new_size: int) -> None:
        """Change window size; existing history takes effect immediately.

        No caller-side rebuild is needed.  Shrinking expires indices from
        the fronts; growing rebuilds the deques from retained raw history.
        """
        if not isinstance(new_size, int) or isinstance(new_size, bool):
            raise TypeError("new_size must be an int")
        if new_size < 0:
            raise ValueError("new_size must be >= 0")

        old_size = self._window_size
        self._window_size = new_size

        if new_size == 0:
            self._min_deque.clear()
            self._max_deque.clear()
            return

        if new_size < old_size:
            # Smaller window: only extra front elements leave.
            self._expire(len(self._values) - new_size)
            return

        if new_size > old_size:
            # Larger window: replay the newest new_size raw values so that
            # elements previously excluded are correctly considered.
            self._rebuild()

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def get_min(self) -> Optional[Any]:
        """Minimum over the current window, or ``None`` if it is empty."""
        self._expire(len(self._values) - self._window_size)
        if not self._min_deque:
            return None
        return self._values[self._min_deque[0]]

    def get_max(self) -> Optional[Any]:
        """Maximum over the current window, or ``None`` if it is empty."""
        self._expire(len(self._values) - self._window_size)
        if not self._max_deque:
            return None
        return self._values[self._max_deque[0]]

    def get_min_max(self) -> Tuple[Optional[Any], Optional[Any]]:
        """Return ``(min, max)``; both are ``None`` for an empty window."""
        self._expire(len(self._values) - self._window_size)
        minimum = self._values[self._min_deque[0]] if self._min_deque else None
        maximum = self._values[self._max_deque[0]] if self._max_deque else None
        return minimum, maximum

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _expire(self, cutoff: int) -> None:
        """Drop indices strictly older than ``cutoff`` from both fronts."""
        min_dq = self._min_deque
        while min_dq and min_dq[0] < cutoff:
            min_dq.popleft()
        max_dq = self._max_deque
        while max_dq and max_dq[0] < cutoff:
            max_dq.popleft()

    def _rebuild(self) -> None:
        """Reconstruct both deques from the currently effective values."""
        start = max(0, len(self._values) - self._window_size)
        min_dq = deque()
        max_dq = deque()
        values = self._values
        for idx in range(start, len(values)):
            value = values[idx]
            while min_dq and values[min_dq[-1]] >= value:
                min_dq.pop()
            min_dq.append(idx)
            while max_dq and values[max_dq[-1]] <= value:
                max_dq.pop()
            max_dq.append(idx)
        self._min_deque = min_dq
        self._max_deque = max_dq
