"""CDC 变更日志解析库（仅标准库）。

日志格式：JSON Lines，每行一个事件，lsn 全局单调递增：

    {"lsn":1,"type":"begin","txid":"t1"}
    {"lsn":2,"type":"insert","txid":"t1","table":"users","id":"u1","after":{...}}
    {"lsn":3,"type":"update","txid":"t1","table":"users","id":"u1","before":{...},"after":{...}}
    {"lsn":4,"type":"delete","txid":"t1","table":"users","id":"u1","before":{...}}
    {"lsn":5,"type":"commit","txid":"t1"}
    {"lsn":6,"type":"abort","txid":"t2"}

约束：同一事务的事件在日志中连续（begin 与 commit/abort 之间不夹杂其他事务的事件）。
日志轮转：changelog-000001.log、changelog-000002.log …… 序号递增。

位点（Position）= (segment, offset, lsn)：
  - segment/offset：已确认到的文件位置，重启后从此处继续读；
  - lsn：已确认事务的 commit LSN 高水位，用于幂等去重（位点被回退时，
    lsn <= 高水位的事件一律跳过，已确认的变更不会重复投递）。
位点只在 confirm() 时原子推进（临时文件 + os.replace），
因此消费语义为「事务级至少一次」：已 confirm 的事务绝不重投，
未 confirm 的事务在重启后整体重投（单个事务内不会部分产出）。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

SEGMENT_RE = re.compile(r"^changelog-(\d{6,})\.log$")


class PositionFallbackError(Exception):
    """位点回退失败：位点指向的段已被轮转清理或截断，需要从快照重新同步。"""


@dataclass(frozen=True)
class Position:
    segment: str
    offset: int
    lsn: int

    def to_dict(self) -> dict:
        return {"segment": self.segment, "offset": self.offset, "lsn": self.lsn}

    @staticmethod
    def from_dict(d: dict) -> "Position":
        return Position(segment=d["segment"], offset=int(d["offset"]), lsn=int(d["lsn"]))


@dataclass
class RecordChange:
    """一条记录在一个事务内的合并结果，sequence 保留原始变更序列。"""

    table: str
    key: str
    kind: str  # insert / update / delete（合并后的净变更类型）
    before: Any  # 该记录在本事务中最早的 before（insert 为 None）
    after: Any  # 该记录在本事务中最新的 after（delete 为 None）
    sequence: List[dict] = field(default_factory=list)


@dataclass
class Transaction:
    txid: str
    commit_lsn: int
    changes: List[RecordChange]
    position: Position  # commit 记录之后的位点，confirm 时落盘


class CheckpointStore:
    """位点持久化，原子写（tmp + fsync + replace）。"""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> Optional[Position]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return Position.from_dict(json.load(f))
        except FileNotFoundError:
            return None

    def save(self, position: Position) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ckpt-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(position.to_dict(), f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def list_segments(log_dir: str) -> List[str]:
    segments = []
    for name in os.listdir(log_dir):
        m = SEGMENT_RE.match(name)
        if m:
            segments.append((int(m.group(1)), name))
    segments.sort()
    return [name for _, name in segments]


def read_events(log_dir: str, position: Optional[Position]) -> Iterator[Tuple[dict, str, int]]:
    """从 position 之后开始读取事件，产出 (event, segment, next_offset)。

    自动跟随日志轮转进入后续段；读不到新数据时自然结束（poll 语义）。
    position 为 None 时从最早的段开头开始。
    """
    segments = list_segments(log_dir)
    if not segments:
        return
    if position is None:
        index, offset = 0, 0
    else:
        if position.segment not in segments:
            raise PositionFallbackError(
                "segment %r 已不存在（最早可用: %r），位点过旧，需从快照重新同步"
                % (position.segment, segments[0])
            )
        index = segments.index(position.segment)
        offset = position.offset
        size = os.path.getsize(os.path.join(log_dir, position.segment))
        if offset > size:
            raise PositionFallbackError(
                "segment %r 长度 %d 小于位点 offset %d，日志被截断"
                % (position.segment, size, offset)
            )
    while index < len(segments):
        segment = segments[index]
        with open(os.path.join(log_dir, segment), "r", encoding="utf-8") as f:
            f.seek(offset)
            while True:
                line = f.readline()  # 流式读取，单行长度不受限
                if not line:
                    break
                offset = f.tell()
                line = line.strip()
                if line:
                    yield json.loads(line), segment, offset
        index += 1
        offset = 0


def merge_ops(ops: List[dict]) -> List[RecordChange]:
    """把同一事务内的行级事件按 (table, id) 合并为最新状态，保留变更序列。

    合并规则：
      - insert 后 update      -> insert（after 取最新）
      - update 后 update      -> update（before 取最早，after 取最新）
      - update/insert 后 delete -> delete（before 取最早）
      - insert 后 delete      -> 净无变更，丢弃
    """
    merged: Dict[Tuple[str, str], RecordChange] = {}
    order: List[Tuple[str, str]] = []
    for op in ops:
        key = (op["table"], op["id"])
        entry = {
            "lsn": op["lsn"],
            "op": op["type"],
            "before": op.get("before"),
            "after": op.get("after"),
        }
        if key not in merged:
            merged[key] = RecordChange(
                table=op["table"],
                key=op["id"],
                kind=op["type"],
                before=op.get("before"),
                after=op.get("after"),
                sequence=[entry],
            )
            order.append(key)
        else:
            rc = merged[key]
            rc.sequence.append(entry)
            rc.after = op.get("after")
    changes = []
    for key in order:
        rc = merged[key]
        first = rc.sequence[0]["op"]
        last = rc.sequence[-1]["op"]
        if first == "insert":
            if last == "delete":
                continue  # 同事务内插入又删除，净无变更
            rc.kind = "insert"
            rc.before = None
        else:
            rc.kind = last
        changes.append(rc)
    return changes


class ChangeParser:
    """变更解析器：从事务日志产出已提交事务，支持断点续传与幂等投递。"""

    def __init__(self, log_dir: str, checkpoint_path: str):
        self.log_dir = log_dir
        self.store = CheckpointStore(checkpoint_path)
        self._position = self.store.load()
        self._confirmed_lsn = self._position.lsn if self._position else 0

    @property
    def position(self) -> Optional[Position]:
        return self._position

    @property
    def confirmed_lsn(self) -> int:
        return self._confirmed_lsn

    def reset(self, position: Optional[Position]) -> None:
        """显式设置位点：用于快照恢复（位点过旧时）或运维位点回退。"""
        if position is None:
            try:
                os.unlink(self.store.path)
            except FileNotFoundError:
                pass
        else:
            self.store.save(position)
        self._position = position
        self._confirmed_lsn = position.lsn if position else 0

    def poll(self) -> Iterator[Transaction]:
        """读取当前可用日志，按提交顺序产出已提交事务（生成器）。

        事务边界：只有读到 commit 才产出，且一个事务的所有变更合并在一个
        Transaction 里一次产出——要么全部产出，要么全不产出；abort 直接丢弃。
        每个产出的事务必须调用 confirm() 才会推进位点。
        """
        pending: Dict[str, List[dict]] = {}
        for event, segment, offset in read_events(self.log_dir, self._position):
            lsn = event.get("lsn", 0)
            if lsn <= self._confirmed_lsn:
                continue  # 幂等：跳过已确认事件（位点回退/重放保护）
            etype = event["type"]
            txid = event.get("txid")
            if etype == "begin":
                pending[txid] = []
            elif etype in ("insert", "update", "delete"):
                pending.setdefault(txid, []).append(event)
            elif etype == "abort":
                pending.pop(txid, None)
            elif etype == "commit":
                ops = pending.pop(txid, [])
                yield Transaction(
                    txid=txid,
                    commit_lsn=lsn,
                    changes=merge_ops(ops),
                    position=Position(segment=segment, offset=offset, lsn=lsn),
                )

    def confirm(self, txn: Transaction) -> None:
        """确认事务已投递，原子推进位点。重复 confirm 是幂等的。"""
        if txn.commit_lsn <= self._confirmed_lsn:
            return
        self.store.save(txn.position)
        self._position = txn.position
        self._confirmed_lsn = txn.commit_lsn


class LogWriter:
    """日志写入器（测试/演示用）：追加事件、维护 LSN、按大小轮转。"""

    def __init__(self, log_dir: str, max_segment_bytes: int = 16 * 1024 * 1024):
        self.log_dir = log_dir
        self.max_segment_bytes = max_segment_bytes
        os.makedirs(log_dir, exist_ok=True)
        segments = list_segments(log_dir)
        if segments:
            self._segment = segments[-1]
            self._offset = os.path.getsize(os.path.join(log_dir, self._segment))
            self._lsn = self._recover_lsn()
        else:
            self._segment = self._segment_name(1)
            self._offset = 0
            self._lsn = 0
        self._txid = 0

    @staticmethod
    def _segment_name(index: int) -> str:
        return "changelog-%06d.log" % index

    def _recover_lsn(self) -> int:
        lsn = 0
        for segment in list_segments(self.log_dir):
            with open(os.path.join(self.log_dir, segment), "rb") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        lsn = max(lsn, json.loads(line)["lsn"])
        return lsn

    def _rotate(self) -> None:
        index = int(SEGMENT_RE.match(self._segment).group(1))
        self._segment = self._segment_name(index + 1)
        self._offset = 0

    def _emit(self, event: dict) -> int:
        self._lsn += 1
        event["lsn"] = self._lsn
        data = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        if self._offset > 0 and self._offset + len(data) > self.max_segment_bytes:
            self._rotate()
        with open(os.path.join(self.log_dir, self._segment), "ab") as f:
            f.write(data)
        self._offset += len(data)
        return self._lsn

    def current_position(self) -> Position:
        """当前写入末尾位点，可作为快照水位。"""
        return Position(segment=self._segment, offset=self._offset, lsn=self._lsn)

    def begin(self, txid: Optional[str] = None) -> str:
        self._txid += 1
        txid = txid or "tx-%d" % self._txid
        self._emit({"type": "begin", "txid": txid})
        return txid

    def insert(self, txid: str, table: str, key: str, after: Any) -> None:
        self._emit({"type": "insert", "txid": txid, "table": table, "id": key, "after": after})

    def update(self, txid: str, table: str, key: str, before: Any, after: Any) -> None:
        self._emit({
            "type": "update", "txid": txid, "table": table,
            "id": key, "before": before, "after": after,
        })

    def delete(self, txid: str, table: str, key: str, before: Any) -> None:
        self._emit({"type": "delete", "txid": txid, "table": table, "id": key, "before": before})

    def commit(self, txid: str) -> None:
        self._emit({"type": "commit", "txid": txid})

    def abort(self, txid: str) -> None:
        self._emit({"type": "abort", "txid": txid})

    def write_txn(self, ops: List[tuple]) -> str:
        """便捷方法：把若干 op 包成一个事务写入。op 形如
        ("insert", table, key, after) / ("update", table, key, before, after) /
        ("delete", table, key, before)。"""
        txid = self.begin()
        for op in ops:
            kind = op[0]
            if kind == "insert":
                self.insert(txid, op[1], op[2], op[3])
            elif kind == "update":
                self.update(txid, op[1], op[2], op[3], op[4])
            elif kind == "delete":
                self.delete(txid, op[1], op[2], op[3])
            else:
                raise ValueError("unknown op %r" % (kind,))
        self.commit(txid)
        return txid
