"""批处理引擎：显式状态机 + 断点续跑 + 幂等副作用（仅标准库）。

每条记录的状态机：

    PENDING --> PROCESSING --> SUCCESS
                  |   |
                  |   +------> FAILED --> PROCESSING (重试)
                  |               |
                  |               +------> SKIPPED (超过最大重试次数)
                  |
                  +--(崩溃恢复)--> PENDING
    PENDING --> SKIPPED (人工跳过)

不变量：
- SUCCESS / SKIPPED 是终态，不允许任何迁出。
- 状态文件中不允许残留 PROCESSING；加载时如发现（说明进程被强杀），
  通过崩溃恢复迁移 PROCESSING -> PENDING，下次运行会重新处理。
- 重跑只处理 PENDING / FAILED 记录，SUCCESS / SKIPPED 绝不触碰，
  因此同一记录的副作用最多产生一次。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from enum import Enum


class Status(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class BatchStatus(str, Enum):
    INCOMPLETE = "incomplete"            # 仍有 PENDING/PROCESSING 记录
    SUCCESS = "success"                  # 全部成功
    PARTIAL_SUCCESS = "partial_success"  # 部分成功（有成功也有失败/跳过）
    FAILED = "failed"                    # 没有任何一条成功


# 正常允许的状态迁移（唯一事实来源）
ALLOWED_TRANSITIONS = {
    Status.PENDING: {Status.PROCESSING, Status.SKIPPED},
    Status.PROCESSING: {Status.SUCCESS, Status.FAILED},
    Status.FAILED: {Status.PROCESSING, Status.SKIPPED},
    Status.SUCCESS: set(),
    Status.SKIPPED: set(),
}

# 崩溃恢复专用迁移：进程被强杀后状态文件里残留的 PROCESSING -> PENDING
RECOVERY_TRANSITIONS = {Status.PROCESSING: {Status.PENDING}}


class IllegalTransitionError(RuntimeError):
    """非法状态迁移。"""


class StateCorruptionError(RuntimeError):
    """持久化状态损坏或违反不变量。"""


class SimulatedCrash(BaseException):
    """测试用：模拟进程在 PROCESSING 期间被强杀（继承 BaseException，不被当普通失败捕获）。"""


class Record:
    __slots__ = ("record_id", "status", "attempts", "error")

    def __init__(self, record_id, status=Status.PENDING, attempts=0, error=None):
        self.record_id = record_id
        self.status = status
        self.attempts = attempts
        self.error = error

    def transition(self, target, *, recovery=False):
        table = RECOVERY_TRANSITIONS if recovery else ALLOWED_TRANSITIONS
        allowed = table.get(self.status, set())
        if target not in allowed:
            kind = "recovery" if recovery else "normal"
            raise IllegalTransitionError(
                f"illegal {kind} transition: {self.record_id} "
                f"{self.status.value} -> {target.value}"
            )
        self.status = target

    def to_dict(self):
        return {
            "id": self.record_id,
            "status": self.status.value,
            "attempts": self.attempts,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data):
        try:
            status = Status(data["status"])
        except (KeyError, ValueError) as exc:
            raise StateCorruptionError(f"bad record status: {data!r}") from exc
        return cls(
            record_id=data["id"],
            status=status,
            attempts=int(data.get("attempts", 0)),
            error=data.get("error"),
        )


class BatchProcessor:
    def __init__(self, batch_id, record_ids, handler, store_path, max_attempts=3):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._batch_id = batch_id
        self._order = list(record_ids)
        self._handler = handler
        self._store_path = store_path
        self._max_attempts = max_attempts
        self.recovered_crashes = 0
        self._records = self._load_or_init()
        self._assert_invariants()

    # ---- 持久化 ----

    def _load_or_init(self):
        if not os.path.exists(self._store_path):
            return {rid: Record(rid) for rid in self._order}
        with open(self._store_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("batch_id") != self._batch_id:
            raise StateCorruptionError(
                f"batch_id mismatch: store={data.get('batch_id')!r} want={self._batch_id!r}"
            )
        records = {}
        for item in data.get("records", []):
            rec = Record.from_dict(item)
            if rec.status is Status.PROCESSING:
                # 进程在 PROCESSING 期间被强杀：崩溃恢复，允许下次重跑
                rec.transition(Status.PENDING, recovery=True)
                self.recovered_crashes += 1
            records[rec.record_id] = rec
        if set(records) != set(self._order):
            raise StateCorruptionError(
                f"record set mismatch: store={sorted(records)} want={sorted(self._order)}"
            )
        return records

    def _persist(self):
        payload = {
            "batch_id": self._batch_id,
            "records": [self._records[rid].to_dict() for rid in self._order],
        }
        # 原子写入，避免写一半被强杀导致状态文件损坏
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self._store_path) or ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._store_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- 不变量 ----

    def _assert_invariants(self):
        for rec in self._records.values():
            if not isinstance(rec.status, Status):
                raise StateCorruptionError(f"{rec.record_id}: invalid status {rec.status!r}")
            if rec.attempts < 0:
                raise StateCorruptionError(f"{rec.record_id}: negative attempts")
            if rec.status is Status.SUCCESS and rec.attempts < 1:
                raise StateCorruptionError(f"{rec.record_id}: SUCCESS without attempt")
            if rec.status is Status.FAILED and rec.error is None:
                raise StateCorruptionError(f"{rec.record_id}: FAILED without error")
            if rec.status is Status.PROCESSING:
                raise StateCorruptionError(
                    f"{rec.record_id}: PROCESSING must not persist across loads"
                )

    # ---- 运行 ----

    def run(self):
        """处理所有未终结的记录（PENDING/FAILED），返回批次摘要。

        SUCCESS / SKIPPED 记录直接跳过 -> 同一记录不会重复产生副作用。
        """
        for rid in self._order:
            rec = self._records[rid]
            if rec.status in (Status.SUCCESS, Status.SKIPPED):
                continue
            rec.transition(Status.PROCESSING)
            rec.attempts += 1
            rec.error = None
            self._persist()
            try:
                self._handler(rid)
            except Exception as exc:  # noqa: BLE001 - 业务失败记为 FAILED
                rec.transition(Status.FAILED)
                rec.error = f"{type(exc).__name__}: {exc}"
                if rec.attempts >= self._max_attempts:
                    rec.transition(Status.SKIPPED)
            else:
                rec.transition(Status.SUCCESS)
                rec.error = None
            self._persist()
            self._assert_invariants()
        return self.summary()

    # ---- 状态汇总 ----

    def batch_status(self):
        statuses = {rec.status for rec in self._records.values()}
        if statuses & {Status.PENDING, Status.PROCESSING}:
            return BatchStatus.INCOMPLETE
        if statuses == {Status.SUCCESS}:
            return BatchStatus.SUCCESS
        if Status.SUCCESS not in statuses:
            return BatchStatus.FAILED
        return BatchStatus.PARTIAL_SUCCESS

    def summary(self):
        counts = Counter(rec.status for rec in self._records.values())
        return {
            "batch_id": self._batch_id,
            "batch_status": self.batch_status().value,
            "total": len(self._order),
            "success": counts.get(Status.SUCCESS, 0),
            "failed": counts.get(Status.FAILED, 0),
            "skipped": counts.get(Status.SKIPPED, 0),
            "pending": counts.get(Status.PENDING, 0),
        }

    def record(self, rid):
        return self._records[rid]
