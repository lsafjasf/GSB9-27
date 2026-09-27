"""合法更新授权日志（trusted-updater journal）。

包管理器/部署流水线在执行合法变更前，先向授权日志登记
（路径 + 预期新内容的 SHA-256）。监控器扫描时若发现内容变化
且新内容哈希与登记一致，则判定为合法更新：不告警，但写入审计
日志（event=authorized_update），实现"抑制但可审计"。

授权为一次性（single-use）：被监控器消费后立即失效。否则攻击者
可以把文件回滚到历史上任一授权过的版本（rollback）来绕过检测。

真实部署中该日志应对监控进程只读、仅包管理器可写（文件权限控制）。
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, Optional


class AuthorizationJournal:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8"):
                pass

    def authorize(self, relpath: str, sha256: str, actor: str = "unknown") -> None:
        rec = {
            "op": "authorize",
            "ts": round(time.time(), 3),
            "path": relpath,
            "sha256": sha256,
            "actor": actor,
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def consume(self, relpath: str, sha256: str) -> None:
        rec = {
            "op": "consume",
            "ts": round(time.time(), 3),
            "path": relpath,
            "sha256": sha256,
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def _outstanding(self) -> Dict[tuple, Dict[str, str]]:
        """重放日志，返回仍未消费的授权 {(path, sha256): 最早的授权记录}。"""
        outstanding: Dict[tuple, Dict[str, str]] = {}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    key = (rec.get("path"), rec.get("sha256"))
                    op = rec.get("op", "authorize")
                    if op == "authorize":
                        outstanding.setdefault(key, rec)
                    elif op == "consume":
                        outstanding.pop(key, None)
        except FileNotFoundError:
            pass
        return outstanding

    def lookup(self, relpath: str, sha256: str) -> Optional[Dict[str, str]]:
        """返回匹配的未消费授权记录；无匹配返回 None。"""
        return self._outstanding().get((relpath, sha256))

    def match_and_consume(self, relpath: str, sha256: str) -> Optional[Dict[str, str]]:
        """原子语义：命中未消费授权则消费之并返回记录，否则返回 None。"""
        rec = self.lookup(relpath, sha256)
        if rec is not None:
            self.consume(relpath, sha256)
        return rec
