"""测试基建：分段日志写入器、收集/应用/崩溃 sink。"""
from __future__ import annotations

import json
import os

from changelog import apply_change


class LogWriter:
    """按段写变更日志，超过 max_segment_bytes 自动轮转。"""

    def __init__(self, directory: str, max_segment_bytes: int = 1 << 20,
                 start_lsn: int = 0, start_index: int = 0) -> None:
        self.directory = directory
        self.max_segment_bytes = max_segment_bytes
        self.lsn = start_lsn
        self._index = start_index
        self._fh = None
        os.makedirs(directory, exist_ok=True)

    @property
    def segment_name(self) -> str:
        return f"segment-{self._index:06d}.log"

    def _ensure_open(self) -> None:
        if self._fh is None:
            self._fh = open(os.path.join(self.directory, self.segment_name), "ab")

    def _rotate(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._index += 1

    def emit(self, **fields) -> int:
        self._ensure_open()
        self.lsn += 1
        record = {"lsn": self.lsn, **fields}
        data = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        if self._fh.tell() > 0 and self._fh.tell() + len(data) > self.max_segment_bytes:
            self._rotate()
            self._ensure_open()
        self._fh.write(data)
        self._fh.flush()
        return self.lsn

    def begin(self, txid):
        return self.emit(txid=txid, op="begin")

    def commit(self, txid):
        return self.emit(txid=txid, op="commit")

    def abort(self, txid):
        return self.emit(txid=txid, op="abort")

    def insert(self, txid, table, pk, after):
        return self.emit(txid=txid, op="insert", table=table, pk=pk, after=after)

    def update(self, txid, table, pk, before, after):
        return self.emit(
            txid=txid, op="update", table=table, pk=pk, before=before, after=after
        )

    def delete(self, txid, table, pk, before):
        return self.emit(txid=txid, op="delete", table=table, pk=pk, before=before)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


class CollectingSink:
    """把投递的事务收集到列表里。"""

    def __init__(self) -> None:
        self.txs = []

    def deliver(self, tx) -> None:
        self.txs.append(tx)


class ApplyingSink:
    """把变更应用到 replica dict。"""

    def __init__(self, replica: dict) -> None:
        self.replica = replica
        self.delivered = 0

    def deliver(self, tx) -> None:
        for change in tx.changes:
            apply_change(self.replica, change)
        self.delivered += 1


class SimulatedCrash(Exception):
    """模拟进程崩溃。"""


class CrashSink(ApplyingSink):
    """应用变更后、确认前按预算崩溃，模拟「已应用未确认」的崩溃窗口。

    budget 表示本次运行允许确认的事务数，用完后下一次 deliver 在
    应用变更之后、返回之前抛 SimulatedCrash（位点不会记录该事务）。
    已确认（confirmed）的事务若被重复投递会直接断言失败。
    """

    def __init__(self, replica: dict, confirmed: set, budget: int) -> None:
        super().__init__(replica)
        self.confirmed = confirmed
        self.budget = budget

    def deliver(self, tx) -> None:
        assert tx.commit_lsn not in self.confirmed, (
            f"duplicate delivery of confirmed tx, commit_lsn={tx.commit_lsn}"
        )
        for change in tx.changes:
            apply_change(self.replica, change)
        if self.budget <= 0:
            raise SimulatedCrash
        self.budget -= 1
        self.confirmed.add(tx.commit_lsn)
        self.delivered += 1
