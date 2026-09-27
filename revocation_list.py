"""可持久化、有上界的令牌撤销列表（仅标准库）。

判定三态：
  - Verdict.NOT_REVOKED : 未撤销（记录存在且未命中，且可排除被清理的可能）
  - Verdict.REVOKED     : 已撤销
  - Verdict.UNKNOWN     : 记录已过期被清理，无法判定（明确策略见 check）

持久化：追加式 JSONL 日志；cleanup 时原子重写为单行快照。
时间：通过 clock 可调用对象注入，便于测试。
"""

from __future__ import annotations

import json
import os
import sys
import time
from enum import Enum
from typing import Callable, Dict, Iterable, Optional, Tuple


class Verdict(Enum):
    NOT_REVOKED = "not_revoked"
    REVOKED = "revoked"
    UNKNOWN = "unknown"  # 记录过期无法判定


def _deep_sizeof(obj, _seen=None) -> int:
    """递归估算对象内存占用（字节），仅标准库。"""
    if _seen is None:
        _seen = set()
    oid = id(obj)
    if oid in _seen:
        return 0
    _seen.add(oid)
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        size += sum(_deep_sizeof(k, _seen) + _deep_sizeof(v, _seen) for k, v in obj.items())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        size += sum(_deep_sizeof(i, _seen) for i in obj)
    return size


class RevocationList:
    """按令牌标识（jti）记录撤销，记录随令牌自然过期而清理，保证内存有上界。

    清理水位（watermark）策略：
      cleanup(now) 会删除所有 exp <= now 的记录，并把水位提升为 now。
      水位单调递增：发生时间回拨（now < 当前水位）时水位不后退，
      保证“已清理”的判定边界不因时钟异常而扩大，重启前后判定一致。
    """

    def __init__(self, path: Optional[str] = None, clock: Callable[[], float] = time.time):
        self._path = path
        self._clock = clock
        self._records: Dict[str, int] = {}  # jti -> 令牌过期时间 exp
        self._watermark: Optional[float] = None  # None 表示从未清理
        if path and os.path.exists(path):
            self._load()

    # ---------------- 查询 ----------------

    def check(self, jti: str, token_exp: Optional[int] = None) -> Verdict:
        """判定单个令牌。

        策略（明确）：
          1. jti 命中记录 -> REVOKED；
          2. 未命中且提供了 token_exp 且 token_exp <= 清理水位
             -> UNKNOWN（该令牌的撤销记录可能已随过期被清理，无法判定，
                调用方应拒绝或走二次校验，切勿当作未撤销放行）；
          3. 其余情况 -> NOT_REVOKED（令牌未过期到水位之前，若被撤销
             记录必然仍在，可安全判定）。
        """
        if jti in self._records:
            return Verdict.REVOKED
        if token_exp is not None and self._watermark is not None and token_exp <= self._watermark:
            return Verdict.UNKNOWN
        return Verdict.NOT_REVOKED

    # ---------------- 写入 ----------------

    def revoke(self, jti: str, exp: int) -> None:
        self.revoke_many([(jti, exp)])

    def revoke_many(self, entries: Iterable[Tuple[str, int]]) -> int:
        """批量撤销。重复撤销幂等：同一 jti 保留最大的 exp（记录存活更久，更安全）。"""
        items = [(str(j), int(e)) for j, e in entries]
        if not items:
            return 0
        for jti, exp in items:
            old = self._records.get(jti)
            if old is None or exp > old:
                self._records[jti] = exp
        if self._path:
            with open(self._path, "a", encoding="utf-8") as fh:
                for jti, exp in items:
                    fh.write(json.dumps({"op": "revoke", "jti": jti, "exp": exp}) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return len(items)

    # ---------------- 清理 ----------------

    def cleanup(self, now: Optional[float] = None) -> int:
        """删除 exp <= now 的记录，返回删除条数。水位单调不减（抗时间回拨）。"""
        if now is None:
            now = self._clock()
        effective = now if self._watermark is None else max(now, self._watermark)
        before = len(self._records)
        self._records = {j: e for j, e in self._records.items() if e > effective}
        self._watermark = effective
        removed = before - len(self._records)
        if self._path:
            self._persist_snapshot()
        return removed

    # ---------------- 观测 ----------------

    @property
    def watermark(self) -> Optional[float]:
        return self._watermark

    def __len__(self) -> int:
        return len(self._records)

    def memory_usage(self) -> dict:
        return {
            "records": len(self._records),
            "watermark": self._watermark,
            "approx_bytes": _deep_sizeof(self._records),
        }

    # ---------------- 持久化 ----------------

    def _persist_snapshot(self) -> None:
        """原子重写为单行快照（tmp + rename），崩溃不会留下半文件。"""
        directory = os.path.dirname(os.path.abspath(self._path))
        tmp = os.path.join(directory, f".{os.path.basename(self._path)}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "op": "snapshot",
                "watermark": self._watermark,
                "records": self._records,
            }) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)

    def _load(self) -> None:
        with open(self._path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                op = entry.get("op")
                if op == "snapshot":
                    self._watermark = entry["watermark"]
                    self._records = {str(k): int(v) for k, v in entry["records"].items()}
                elif op == "revoke":
                    jti, exp = str(entry["jti"]), int(entry["exp"])
                    old = self._records.get(jti)
                    if old is None or exp > old:
                        self._records[jti] = exp
                else:
                    raise ValueError(f"unknown op in log: {op!r}")
