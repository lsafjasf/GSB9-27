"""Small statistics helpers (standard library only)."""

from __future__ import annotations

import math
from typing import Sequence


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolation percentile (same convention as numpy ``linear``)."""

    if not 0 <= p <= 100:
        raise ValueError("p must be in [0, 100]")
    if not values:
        raise ValueError("percentile of empty sequence")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (p / 100.0) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def distribution(values: Sequence[float]) -> dict[str, float]:
    """Completion-time distribution summary used in the report."""

    if not values:
        return {
            "count": 0,
            "min": 0.0,
            "p50": 0.0,
            "mean": 0.0,
            "p90": 0.0,
            "p99": 0.0,
            "max": 0.0,
            "std": 0.0,
        }
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return {
        "count": float(len(values)),
        "min": min(values),
        "p50": percentile(values, 50),
        "mean": mean,
        "p90": percentile(values, 90),
        "p99": percentile(values, 99),
        "max": max(values),
        "std": var**0.5,
    }
