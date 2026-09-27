"""把记录级变更应用到下游副本（dict），净状态应用天然幂等。"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from .events import RecordChange


def apply_change(replica: Dict[Tuple[str, str], Dict[str, Any]], change: RecordChange) -> None:
    key = (change.table, change.pk)
    if change.op in ("insert", "update"):
        replica[key] = change.after
    elif change.op == "delete":
        replica.pop(key, None)
    # op == "none"：事务内先插后删，净效果为空
