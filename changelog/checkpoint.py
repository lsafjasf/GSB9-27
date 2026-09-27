"""消费位点（checkpoint）的持久化：原子写，支持重启续传。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class Position:
    """消费位点。

    segment/offset 始终落在某条 commit/abort 记录之后，即事务边界上；
    last_lsn 为已确认投递的最大 commit lsn，用于重启后去重。
    """

    segment: str
    offset: int
    last_lsn: int = 0
    last_txid: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "Position":
        obj = json.loads(text)
        return Position(
            segment=obj["segment"],
            offset=int(obj["offset"]),
            last_lsn=int(obj.get("last_lsn", 0)),
            last_txid=int(obj.get("last_txid", 0)),
        )


class CheckpointStore:
    """位点写盘采用 tmp + fsync + rename，崩溃不会留下半个文件。"""

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> Optional[Position]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return Position.from_json(fh.read())
        except FileNotFoundError:
            return None

    def save(self, position: Position) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(position.to_json())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
