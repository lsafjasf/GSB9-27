"""Sliding-window min/max structure backed by monotonic deques.

Public API:
    SlidingWindowMinMax(window_size)
        .push(value)
        .resize(new_size)
        .get_min() / get_max() / get_min_max()
        .window_size / count / active_count
"""

from .sliding_minmax import SlidingWindowMinMax

__all__ = ["SlidingWindowMinMax"]
