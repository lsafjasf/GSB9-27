"""审计日志：所有被排除/被抑制的完整性事件都落盘，保证排除行为可审计。"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional


class AuditLog:
    """JSONL 审计日志。path 为 None 时仅驻留内存（便于测试）。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.records: list = []
        self._fh = None
        if path is not None:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self._fh = open(path, "a", encoding="utf-8")

    def record(self, event: str, path: str, **fields: Any) -> Dict[str, Any]:
        rec: Dict[str, Any] = {
            "ts": round(time.time(), 3),
            "event": event,
            "path": path,
        }
        rec.update(fields)
        self.records.append(rec)
        if self._fh is not None:
            self._fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            self._fh.flush()
        return rec

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
