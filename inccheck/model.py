"""Shared data model for the incremental check engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


class Status:
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Target:
    """One unit of work: a check applied to a path (path=None => global)."""

    check_id: str
    path: Optional[str]

    def label(self) -> str:
        return self.path if self.path is not None else "<repo>"


@dataclass
class Finding:
    check_id: str
    target: Optional[str]
    status: str
    message: str = ""


@dataclass
class Change:
    """A single changed file, relative to the repo root.

    status: "A" (added), "M" (modified), "D" (deleted)
    """

    status: str
    path: str


@dataclass
class Plan:
    mode: str  # "incremental" | "full"
    targets: List[Target] = field(default_factory=list)
    fallback_reasons: List[str] = field(default_factory=list)
    per_change: List[Tuple[Change, List[Target]]] = field(default_factory=list)
    total_targets: int = 0
