"""Shard construction strategies.

:func:`split_cost_balanced` implements the classic LPT (Longest Processing
Time first, Graham 1969) greedy cut: sort items by descending estimated cost
and always append the next item to the currently lightest shard. It minimises
the maximum shard load among simple online heuristics and guarantees

    LPT_makespan / OPT_makespan <= 4/3 - 1/(3k)

which is exactly the bound that keeps straggler shards rare.

The splitter is also used to *re-split* the residual items of a failed shard,
so the retry only touches work that was never completed.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from .models import Item, Shard


def _normalise(items: Iterable[Item], shard_count: int) -> list[Item]:
    items = list(items)
    if len({item.item_id for item in items}) != len(items):
        raise ValueError("item_id values must be unique within one split")
    if shard_count < 1:
        raise ValueError("shard_count must be >= 1")
    return items


def split_cost_balanced(
    items: Iterable[Item],
    shard_count: int,
    *,
    prefix: str = "s",
    depth: int = 0,
    parent_id: str | None = None,
) -> list[Shard]:
    """Cut ``items`` into at most ``shard_count`` cost-balanced shards (LPT).

    When there are fewer items than shards, empty shards are not produced:
    the result has ``min(shard_count, len(items))`` non-empty shards. That case
    (shards > tasks) is required by the specification and must not invent work.
    """

    items = _normalise(items, shard_count)
    k = min(shard_count, len(items))
    if k == 0:
        return []

    # Descending cost; id tie-break makes output deterministic.
    ordered = sorted(items, key=lambda it: (-it.cost, it.item_id))
    buckets: list[list[Item]] = [[] for _ in range(k)]
    loads = [0.0] * k
    for item in ordered:
        target = min(range(k), key=lambda idx: (loads[idx], idx))
        buckets[target].append(item)
        loads[target] += item.cost

    shards: list[Shard] = []
    tag = f"d{depth}-" if depth else ""
    for idx, bucket in enumerate(buckets):
        if not bucket:
            continue
        shards.append(
            Shard(
                shard_id=f"{prefix}.{tag}{idx}",
                items=tuple(bucket),
                depth=depth,
                parent_id=parent_id,
            )
        )
    return shards


def split_even(
    items: Iterable[Item],
    shard_count: int,
    *,
    prefix: str = "e",
) -> list[Shard]:
    """Naive baseline: fixed-size chunks ignoring cost.

    Used only to quantify how much the cost-aware cut improves balance.
    """

    items = _normalise(items, shard_count)
    if not items:
        return []
    size = (len(items) + shard_count - 1) // shard_count
    shards: list[Shard] = []
    for start in range(0, len(items), size):
        chunk = items[start : start + size]
        shards.append(
            Shard(shard_id=f"{prefix}.{len(shards)}", items=tuple(chunk))
        )
    return shards


def child_shard_count(residual_items: int, depth: int, base_shard_count: int) -> int:
    """How many children a failed shard is broken into on retry.

    The cut gets smaller as the retry depth grows (half the base per level,
    minimum one), bounded by the number of residual items so we never create
    empty shards.
    """

    wanted = max(1, base_shard_count // (2**depth))
    return max(1, min(wanted, residual_items))


def split_quality(
    shards: Sequence[Shard],
    total_cost: float | None = None,
) -> dict[str, float]:
    """Quality indicators for one cut.

    Returns a dict with:

    - ``shards_requested`` / ``shards_nonempty``
    - ``max_load`` / ``min_load`` / ``mean_load`` / ``std_load``
    - ``imbalance_ratio``: (max-mean)/mean; 0 for a perfectly even cut
    - ``straggler_overhead``: makespan / mean_load - 1, the wall-clock penalty
      paid versus ideal parallelism
    - ``utilisation_lower_bound``: mean_load / max_load, a 0..1 efficiency
    - ``lpt_bound_vs_lb``: max_load / (total_cost/k) - 1, gap to the trivial
      average lower bound on the optimal makespan
    """

    loads = [shard.estimated_cost for shard in shards]
    k = len(loads)
    if k == 0:
        return {
            "shards_requested": 0.0,
            "shards_nonempty": 0.0,
            "max_load": 0.0,
            "min_load": 0.0,
            "mean_load": 0.0,
            "std_load": 0.0,
            "imbalance_ratio": 0.0,
            "straggler_overhead": 0.0,
            "utilisation_lower_bound": 1.0,
            "lpt_bound_vs_lb": 0.0,
        }

    total = total_cost if total_cost is not None else float(sum(loads))
    mean = total / k
    variance = sum((load - mean) ** 2 for load in loads) / k
    max_load, min_load = max(loads), min(loads)

    def ratio(num: float, den: float) -> float:
        return num / den if den else 0.0

    return {
        "shards_requested": float(k),
        "shards_nonempty": float(sum(1 for load in loads if load > 0)),
        "max_load": max_load,
        "min_load": min_load,
        "mean_load": mean,
        "std_load": variance**0.5,
        "imbalance_ratio": ratio(max_load - mean, mean),
        "straggler_overhead": ratio(max_load, mean) - 1.0,
        "utilisation_lower_bound": ratio(mean, max_load),
        "lpt_bound_vs_lb": ratio(max_load, mean) - 1.0,
    }
