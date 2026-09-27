"""修复前的原始监控器（保留用于对照与复现）。

问题：
1. 只看 (size, mtime, mode) 元数据，不看内容 —— 攻击者保持大小
   与 mtime 不变的篡改完全漏检（漏报）。
2. 没有排除规则 —— 临时文件、构建产物、轮转日志全部触发告警。
3. 权限变化、touch、原子替换、删除重建全部触发告警（误报）。
4. 合法更新（包管理器）与真实篡改在告警里无法区分。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LegacyEntry:
    size: int
    mtime_ns: int
    mode: int


@dataclass
class LegacyAlert:
    path: str
    kind: str  # CREATED / DELETED / MODIFIED
    detail: Dict[str, Any] = field(default_factory=dict)


class LegacyMonitor:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self._state: Dict[str, LegacyEntry] = {}
        self._baselined = False

    def baseline(self) -> None:
        self.scan()
        self._baselined = True

    def scan(self) -> List[LegacyAlert]:
        alerts: List[LegacyAlert] = []
        seen: set[str] = set()
        first = not self._baselined

        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, self.root).replace(os.sep, "/")
                seen.add(rel)
                try:
                    st = os.stat(full)
                except FileNotFoundError:
                    continue
                if not os.path.isfile(full):
                    continue
                cur = LegacyEntry(st.st_size, st.st_mtime_ns, st.st_mode)
                old = self._state.get(rel)
                if first:
                    self._state[rel] = cur
                elif old is None:
                    alerts.append(LegacyAlert(rel, "CREATED", {"size": st.st_size}))
                    self._state[rel] = cur
                elif (cur.size, cur.mtime_ns, cur.mode) != (old.size, old.mtime_ns, old.mode):
                    alerts.append(LegacyAlert(rel, "MODIFIED", {
                        "old_size": old.size, "new_size": cur.size,
                        "old_mtime_ns": old.mtime_ns, "new_mtime_ns": cur.mtime_ns,
                        "old_mode": old.mode, "new_mode": cur.mode,
                    }))
                    self._state[rel] = cur

        if not first:
            for rel in sorted(set(self._state) - seen):
                self._state.pop(rel)
                alerts.append(LegacyAlert(rel, "DELETED", {}))

        self._baselined = True
        return alerts
