"""Exact progress tracking.

Golden rule (required by the specification):

    progress = completed_work / total_work

It is *never* approximated by ``completed_shards / total_shards``. Shards can
hold wildly different amounts of work (the "single extremely long shard" case
is in the test suite), so a shard-count bar would visibly lie.

Work accounting uses the *same cost measure used for splitting* (data volume,
historical duration, ...). Every item contributes its fixed ``cost`` to the
total; when an item is successfully committed its cost moves into completed
work. A failed attempt commits nothing, so failures keep the bar flat instead
of inflating or rewinding it; the wall-clock time burned by retries is
tracked separately by the executor as ``wasted_work``.

Dynamically added items grow ``total_work`` exactly as they grow the batch, so
the bar stays correct after late arrivals too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Item


@dataclass(frozen=True)
class Snapshot:
    tick: float
    completed_work: float
    total_work: float
    completed_items: int
    total_items: int

    @property
    def fraction(self) -> float:
        if self.total_work > 0:
            return self.completed_work / self.total_work
        # Degenerate batch: every item has zero cost.
        return 1.0 if self.total_items and self.completed_items == self.total_items else 0.0


class ProgressTracker:
    def __init__(self, items: Iterable[Item] = ()):
        self._cost: dict[str, float] = {}
        self._completed: set[str] = set()
        for item in items:
            self.add_items([item])

    # -- mutators ---------------------------------------------------------

    def add_items(self, items: Iterable[Item]) -> None:
        for item in items:
            if item.item_id in self._cost:
                raise ValueError(f"duplicate item_id: {item.item_id}")
            self._cost[item.item_id] = item.cost

    def mark_completed(self, item_id: str) -> None:
        if item_id not in self._cost:
            raise KeyError(f"unknown item: {item_id}")
        if item_id in self._completed:
            # Double commit means reprocessing finished work: fail loudly.
            raise AssertionError(f"item {item_id} committed twice")
        self._completed.add(item_id)

    # -- reads ------------------------------------------------------------

    @property
    def total_items(self) -> int:
        return len(self._cost)

    @property
    def total_work(self) -> float:
        return sum(self._cost.values())

    @property
    def completed_items(self) -> int:
        return len(self._completed)

    @property
    def completed_work(self) -> float:
        return sum(self._cost[item_id] for item_id in self._completed)

    def fraction(self) -> float:
        if self.total_work > 0:
            return self.completed_work / self.total_work
        return 1.0 if self._cost and self.completed_items == self.total_items else 0.0

    def snapshot(self, tick: float) -> Snapshot:
        return Snapshot(
            tick=tick,
            completed_work=self.completed_work,
            total_work=self.total_work,
            completed_items=self.completed_items,
            total_items=self.total_items,
        )

    def assert_consistent(self, completed_log: Iterable[str]) -> None:
        """Brute-force invariant check used directly by the tests.

        Recomputes progress from raw item costs and asserts the tracker value
        matches ``completed_work / total_work`` exactly, plus monotonic
        bookkeeping invariants (no double commits, 0..1 range, exact 1.0 only
        when every item is done).
        """

        completed = set(completed_log)
        assert completed == self._completed, (
            f"completed set drift: log={len(completed)} "
            f"tracker={len(self._completed)}"
        )
        total = sum(self._cost.values())
        numer = sum(self._cost[i] for i in completed)
        expected = numer / total if total > 0 else (
            1.0 if self._cost and len(completed) == len(self._cost) else 0.0
        )
        assert abs(self.completed_work - numer) < 1e-9
        assert abs(self.total_work - total) < 1e-9
        assert abs(self.fraction() - expected) < 1e-9, (
            f"progress must equal completed_work/total_work: "
            f"{self.fraction()} != {expected}"
        )
        assert 0.0 - 1e-9 <= expected <= 1.0 + 1e-9
        if completed and len(completed) == len(self._cost):
            assert abs(expected - 1.0) < 1e-9
        elif not completed:
            assert abs(expected) < 1e-9 or total == 0.0
