"""端到端管线：分段读取 -> 事务装配 -> 记录合并 -> 投递 -> 记录位点。

投递语义：sink.deliver(tx) 正常返回即视为「已确认」，确认后立即持久化位点。
重启后 commit_lsn <= last_lsn 的事务会被跳过，已确认的变更绝不重复投递。
"""
from __future__ import annotations

import os
from typing import Optional

from .checkpoint import CheckpointStore, Position
from .events import parse_line
from .parser import TransactionParser
from .reader import DEFAULT_MAX_LINE_BYTES, SegmentReader


class PositionRollbackError(RuntimeError):
    """位点指向的段已被清理或偏移越界，且 on_rollback='error' 时抛出。"""


class ChangeLogPipeline:
    def __init__(
        self,
        directory: str,
        checkpoint_path: str,
        sink,
        *,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
        on_rollback: str = "earliest",
    ) -> None:
        if on_rollback not in ("earliest", "error"):
            raise ValueError("on_rollback must be 'earliest' or 'error'")
        self.reader = SegmentReader(directory, max_line_bytes=max_line_bytes)
        self.store = CheckpointStore(checkpoint_path)
        self.sink = sink
        self.on_rollback = on_rollback
        self._last_lsn = 0

    def _resolve_start(self) -> Optional[Position]:
        segments = self.reader.list_segments()
        saved = self.store.load()
        self._last_lsn = saved.last_lsn if saved else 0
        if not segments:
            return None
        if saved is None:
            return Position(segment=segments[0], offset=0)
        valid = saved.segment in segments
        if valid:
            size = os.path.getsize(os.path.join(self.reader.directory, saved.segment))
            valid = 0 <= saved.offset <= size
        if valid:
            return saved
        if self.on_rollback == "error":
            raise PositionRollbackError(
                f"checkpoint points to {saved.segment}:{saved.offset}, "
                "which is no longer available"
            )
        # 位点回退：从最早可用段重读，靠 last_lsn 去重，已确认事务不会重复投递
        return Position(segment=segments[0], offset=0, last_lsn=saved.last_lsn)

    def run_until_caught_up(self) -> int:
        """消费当前所有完整日志，返回本次投递的事务数。"""
        start = self._resolve_start()
        if start is None:
            return 0
        self.reader.open_at(start.segment, start.offset)
        parser = TransactionParser()
        delivered = 0
        try:
            while True:
                item = self.reader.readline()
                if item is None:
                    break
                line, segment, offset = item
                event = parse_line(line)
                tx = parser.feed(event)
                if tx is None:
                    continue
                if tx.commit_lsn > self._last_lsn:
                    self.sink.deliver(tx)  # 正常返回 = 已确认
                    delivered += 1
                self._last_lsn = max(self._last_lsn, tx.commit_lsn)
                self.store.save(
                    Position(
                        segment=segment,
                        offset=offset,
                        last_lsn=self._last_lsn,
                        last_txid=tx.txid,
                    )
                )
        finally:
            self.reader.close()
        return delivered
