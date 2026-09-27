"""变更日志的事件模型与行解析。

日志为按行存储的 JSON 对象，每行一个事件：

    {"lsn": 1, "txid": 100, "op": "begin"}
    {"lsn": 2, "txid": 100, "op": "insert", "table": "users", "pk": "1",
     "after": {"name": "a"}}
    {"lsn": 3, "txid": 100, "op": "update", "table": "users", "pk": "1",
     "before": {"name": "a"}, "after": {"name": "b"}}
    {"lsn": 4, "txid": 100, "op": "delete", "table": "users", "pk": "1",
     "before": {"name": "b"}}
    {"lsn": 5, "txid": 100, "op": "commit"}

op 取值：begin / insert / update / delete / commit / abort。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


class ParseError(ValueError):
    """日志行无法解析时抛出。"""


DML_OPS = ("insert", "update", "delete")
CTL_OPS = ("begin", "commit", "abort")


@dataclass(frozen=True)
class RawEvent:
    lsn: int
    txid: int
    op: str
    table: Optional[str] = None
    pk: Optional[str] = None
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class RecordChange:
    """同一记录在一个事务内多次变更合并后的净结果。

    op 为净操作：insert / update / delete / none（事务内先插后删，净效果为空）。
    history 保留该记录完整的变更序列（按日志顺序）。
    """

    table: str
    pk: str
    op: str
    before: Optional[Dict[str, Any]]
    after: Optional[Dict[str, Any]]
    history: Tuple[RawEvent, ...]


@dataclass(frozen=True)
class Transaction:
    """一个已提交事务的全部记录级变更（commit 时一次性产出）。"""

    txid: int
    commit_lsn: int
    changes: Tuple[RecordChange, ...]


def parse_line(line: bytes) -> RawEvent:
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError(f"log line is not valid utf-8: {exc}") from exc
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"log line is not valid json: {exc}") from exc
    if not isinstance(obj, dict):
        raise ParseError("log line must be a JSON object")
    try:
        lsn = int(obj["lsn"])
        txid = int(obj["txid"])
        op = str(obj["op"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError(f"missing or invalid required field: {exc}") from exc
    if op not in DML_OPS + CTL_OPS:
        raise ParseError(f"unknown op: {op!r}")
    event = RawEvent(
        lsn=lsn,
        txid=txid,
        op=op,
        table=obj.get("table"),
        pk=obj.get("pk"),
        before=obj.get("before"),
        after=obj.get("after"),
    )
    if op in DML_OPS and (event.table is None or event.pk is None):
        raise ParseError(f"dml op {op!r} requires 'table' and 'pk'")
    return event
