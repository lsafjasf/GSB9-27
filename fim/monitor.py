"""修复后的文件完整性监控器。

检出依据：文件内容的 SHA-256，而不是大小 / mtime / 权限等元数据。
- 内容变了才是真正的完整性事件；大小与 mtime 未变的篡改依然可检出。
- 内容不变的权限变化、原子重建、touch 等只进审计日志，不产生告警
  （权限变化可用 alert_on_permission_change=True 提升为告警）。
- 排除规则显式声明；命中规则而被跳过的 create/delete/replace
  事件一律写入审计日志（rule_hit 字段），可审计、可追溯。
- 合法更新经授权日志预登记后抑制，但同样写入审计日志。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .audit import AuditLog
from .authjournal import AuthorizationJournal
from .rules import RuleSet, DEFAULT_RULES

_HASH_CHUNK = 64 * 1024


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Entry:
    sha256: Optional[str]
    size: int
    mtime_ns: int
    mode: int
    ino: int
    dev: int
    excluded: bool = False
    rule_id: Optional[str] = None


@dataclass
class Alert:
    path: str
    kind: str  # CREATED / DELETED / CONTENT_CHANGED
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "detail": self.detail}


class IntegrityMonitor:
    def __init__(
        self,
        root: str,
        rules: Optional[RuleSet] = None,
        audit: Optional[AuditLog] = None,
        auth: Optional[AuthorizationJournal] = None,
        alert_on_permission_change: bool = False,
    ):
        self.root = os.path.abspath(root)
        self.rules = rules if rules is not None else RuleSet(DEFAULT_RULES)
        self.audit = audit if audit is not None else AuditLog(None)
        self.auth = auth
        self.alert_on_permission_change = alert_on_permission_change
        self._state: Dict[str, Entry] = {}
        self._baselined = False

    # ---- 扫描 ----
    def baseline(self) -> None:
        """建立初始基线：不产生任何告警。"""
        self.scan()
        self._baselined = True

    def scan(self) -> List[Alert]:
        alerts: List[Alert] = []
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
                alert = self._inspect(rel, full, st, first)
                if alert is not None:
                    alerts.append(alert)

        if not first:
            for rel in sorted(set(self._state) - seen):
                alert = self._inspect_deleted(rel)
                if alert is not None:
                    alerts.append(alert)

        self._baselined = True
        return alerts

    # ---- 单文件分类 ----
    def _inspect(self, rel: str, full: str, st: os.stat_result, first: bool) -> Optional[Alert]:
        rule = self.rules.match(rel)
        old = self._state.get(rel)

        if rule is not None:
            # 排除路径：刷新状态，不计算哈希；只把"事件级"命中写审计。
            cur = Entry(None, st.st_size, st.st_mtime_ns, st.st_mode, st.st_ino, st.st_dev,
                        excluded=True, rule_id=rule.rule_id)
            if first:
                self._state[rel] = cur
                return None
            if old is None:
                self.audit.record("excluded_created", rel, rule_hit=rule.rule_id,
                                  reason=rule.reason, size=st.st_size)
            elif old.ino != st.st_ino or old.size != st.st_size:
                self.audit.record("excluded_replaced", rel, rule_hit=rule.rule_id,
                                  reason=rule.reason, size=st.st_size)
            self._state[rel] = cur
            return None

        digest = sha256_file(full)
        cur = Entry(digest, st.st_size, st.st_mtime_ns, st.st_mode, st.st_ino, st.st_dev)

        if first:
            self._state[rel] = cur
            return None

        if old is None:
            authed = self._auth_match(rel, digest)
            if authed is not None:
                self.audit.record("authorized_create", rel, actor=authed.get("actor"),
                                  sha256=digest)
            else:
                alert = Alert(rel, "CREATED", {"sha256": digest, "size": st.st_size})
                self._state[rel] = cur
                return alert
            self._state[rel] = cur
            return None

        if digest == old.sha256:
            # 内容相同：任何元数据变化都不是完整性事件。
            if st.st_mode != old.mode:
                self.audit.record("permission_changed", rel,
                                  old_mode=f"{old.mode & 0o7777:04o}",
                                  new_mode=f"{st.st_mode & 0o7777:04o}")
                if self.alert_on_permission_change:
                    self._state[rel] = cur
                    return Alert(rel, "PERMISSION_CHANGED",
                                 {"old_mode": f"{old.mode & 0o7777:04o}",
                                  "new_mode": f"{st.st_mode & 0o7777:04o}"})
            if st.st_ino != old.ino:
                # 原子替换/删除重建后内容逐字节相同。
                self.audit.record("atomic_rewrite_same_content", rel, old_ino=old.ino,
                                  new_ino=st.st_ino, sha256=digest)
            self._state[rel] = cur
            return None

        # 内容不同 —— 唯一真正的完整性事件来源。
        authed = self._auth_match(rel, digest)
        method = "atomic_rename" if st.st_ino != old.ino else "in_place"
        if authed is not None:
            self.audit.record("authorized_update", rel, actor=authed.get("actor"),
                              old_sha256=old.sha256, new_sha256=digest, method=method)
            self._state[rel] = cur
            return None

        self._state[rel] = cur
        return Alert(rel, "CONTENT_CHANGED", {
            "old_sha256": old.sha256,
            "new_sha256": digest,
            "old_size": old.size,
            "new_size": st.st_size,
            "method": method,
        })

    def _inspect_deleted(self, rel: str) -> Optional[Alert]:
        old = self._state.pop(rel)
        if old.excluded:
            self.audit.record("excluded_deleted", rel, rule_hit=old.rule_id)
            return None
        authed = self._auth_match(rel, "")
        if authed is not None:
            self.audit.record("authorized_delete", rel, actor=authed.get("actor"))
            return None
        return Alert(rel, "DELETED", {"old_sha256": old.sha256})

    def _auth_match(self, rel: str, digest: str) -> Optional[Dict[str, str]]:
        if self.auth is None:
            return None
        return self.auth.match_and_consume(rel, digest)
