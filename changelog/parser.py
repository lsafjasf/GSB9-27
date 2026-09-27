"""把事件流装配成事务：事务边界内要么全部产出，要么全不产出。"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .events import RawEvent, Transaction
from .merger import merge_transaction


class TransactionParser:
    """流式事务装配器。

    - DML 事件按 txid 缓冲，commit 时一次性产出 Transaction；
    - abort 丢弃该事务全部缓冲，无任何产出；
    - 未收到 commit/abort 的事务不会有任何产出。
    """

    def __init__(self) -> None:
        self._pending: Dict[int, List[RawEvent]] = {}

    def feed(self, event: RawEvent) -> Optional[Transaction]:
        if event.op == "begin":
            self._pending.setdefault(event.txid, [])
            return None
        if event.op in ("insert", "update", "delete"):
            self._pending.setdefault(event.txid, []).append(event)
            return None
        if event.op == "abort":
            self._pending.pop(event.txid, None)
            return None
        if event.op == "commit":
            events = self._pending.pop(event.txid, [])
            return Transaction(
                txid=event.txid,
                commit_lsn=event.lsn,
                changes=tuple(merge_transaction(events)),
            )
        raise ValueError(f"unexpected op {event.op!r}")

    @property
    def pending_txids(self) -> Tuple[int, ...]:
        return tuple(self._pending)
