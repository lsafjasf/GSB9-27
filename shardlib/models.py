"""Core data model.

A batch is a collection of :class:`Item`. Every item carries an estimated
``cost`` (data size, historical duration, ...). The executor measures real
progress by the *actual work* consumed, never by the number of shards done.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Item:
    """One unit of work.

    Attributes:
        item_id: unique identifier within the batch.
        cost: estimated cost; must be >= 0. Zero is allowed (empty work).
        payload: arbitrary user data.
    """

    item_id: str
    cost: float = 1.0
    payload: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id:
            raise ValueError("item_id must be a non-empty string")
        if self.cost < 0:
            raise ValueError(f"cost must be >= 0, got {self.cost!r}")


@dataclass(frozen=True)
class Failure:
    """Result returned by a handler when processing an item fails."""

    error: str
    spent_work: float = 0.0

    def __post_init__(self) -> None:
        if self.spent_work < 0:
            raise ValueError("spent_work must be >= 0")


@dataclass(frozen=True)
class Shard:
    """An immutable group of items handed to one worker at one time.

    ``depth`` starts at 0 for the initial cut and grows by one every time the
    shard is a child produced by re-splitting a failed shard.
    """

    shard_id: str
    items: tuple[Item, ...]
    depth: int = 0
    parent_id: str | None = None

    @property
    def estimated_cost(self) -> float:
        return sum(item.cost for item in self.items)

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"Shard({self.shard_id}, n={len(self.items)}, depth={self.depth})"
