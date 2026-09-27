"""把同一记录在一个事务内的多次变更合并为最新状态（保留变更序列）。"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .events import RawEvent, RecordChange


def merge_record_events(events: Sequence[RawEvent]) -> RecordChange:
    if not events:
        raise ValueError("no events to merge")
    first, last = events[0], events[-1]
    if first.op == "insert" and last.op == "delete":
        # 事务内先插入又删除：净效果为空
        op, before, after = "none", None, None
    elif first.op == "insert":
        op, before, after = "insert", None, last.after
    elif last.op == "delete":
        op, before, after = "delete", first.before, None
    elif first.op == "delete":
        # 删除后重新插入：净效果为 update（整行替换）
        op, before, after = "update", first.before, last.after
    else:
        op, before, after = "update", first.before, last.after
    return RecordChange(
        table=first.table,
        pk=first.pk,
        op=op,
        before=before,
        after=after,
        history=tuple(events),
    )


def merge_transaction(events: Sequence[RawEvent]) -> List[RecordChange]:
    """按 (table, pk) 分组并保持首次出现的顺序。"""
    order: List[Tuple[str, str]] = []
    grouped: Dict[Tuple[str, str], List[RawEvent]] = {}
    for ev in events:
        key = (ev.table, ev.pk)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(ev)
    return [merge_record_events(grouped[key]) for key in order]
