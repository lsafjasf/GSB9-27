"""变更日志解析库：从变更日志还原记录级变更，支持断点续传与事务边界。"""
from .checkpoint import CheckpointStore, Position
from .events import (
    ParseError,
    RawEvent,
    RecordChange,
    Transaction,
    parse_line,
)
from .merger import merge_record_events, merge_transaction
from .parser import TransactionParser
from .pipeline import ChangeLogPipeline, PositionRollbackError
from .reader import DEFAULT_MAX_LINE_BYTES, OversizedLineError, SegmentReader
from .replica import apply_change

__all__ = [
    "ChangeLogPipeline",
    "CheckpointStore",
    "DEFAULT_MAX_LINE_BYTES",
    "OversizedLineError",
    "ParseError",
    "Position",
    "PositionRollbackError",
    "RawEvent",
    "RecordChange",
    "SegmentReader",
    "Transaction",
    "TransactionParser",
    "apply_change",
    "merge_record_events",
    "merge_transaction",
    "parse_line",
]
