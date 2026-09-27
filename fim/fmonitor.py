"""Fixed file-integrity monitor.

Design fixes vs :mod:`fim.baseline`:

1. Content identity is a SHA-256 hash, not (size, mtime, mode). Size/mtime
   are recorded for forensics only. A same-size replacement with forged
   timestamps is therefore detected.
2. Exclusion rules are declarative (fim/rules.json) and *auditable*: a rule
   hit suppresses an alert but always writes a ``rule_hit`` audit record.
3. Legitimate updates must be admitted *before* they happen (``admit``),
   with operator/reason metadata; admissions are single-use and audited.
4. Permission-only changes no longer raise content-tamper alerts. They are
   recorded as ``perms_changed`` audit events; dangerous new permission
   bits escalate to a real alert.
5. Delete + recreate and atomic rename are followed by path; inode changes
   alone never alert, and identical-content replacement is informational.
"""
from __future__ import annotations

import json
import os
import stat
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import fsutil
from .rules import Rule, RuleSet

# Alert kinds (high signal, paged).
TAMPER_CONTENT = "tamper_content"
TAMPER_DELETED = "tamper_deleted"
TAMPER_PERMS = "tamper_perms_dangerous"
UNEXPECTED_CREATED = "unexpected_created"

# Audit-only kinds (recorded, never paged as content tampering).
RULE_HIT = "rule_hit"
PERMS_CHANGED = "perms_changed"
ADMITTED_UPDATE = "admitted_update"
IDENTICAL_REPLACE = "identical_replace"
SCAN_INFO = "scan_info"
SCAN_ERROR = "scan_error"

_DANGEROUS_BITS = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
_WORLD_WRITABLE = 0o002


@dataclass
class Event:
    kind: str
    path: str
    detail: dict = field(default_factory=dict)

    @property
    def is_alert(self) -> bool:
        return self.kind in {
            TAMPER_CONTENT,
            TAMPER_DELETED,
            TAMPER_PERMS,
            UNEXPECTED_CREATED,
        }


@dataclass
class ScanResult:
    alerts: List[Event] = field(default_factory=list)
    audit: List[Event] = field(default_factory=list)


class FixedMonitor:
    def __init__(
        self,
        root: str,
        state_path: str,
        rules: Optional[RuleSet] = None,
        rules_path: Optional[str] = None,
    ):
        self.root = os.path.abspath(root)
        self.state_path = state_path
        os.makedirs(os.path.dirname(os.path.abspath(state_path)), exist_ok=True)
        self.audit_path = os.path.join(os.path.dirname(os.path.abspath(state_path)), "audit.log")
        self.rules = rules if rules is not None else RuleSet.load(rules_path)
        doc = self._load_state()
        self.state: Dict[str, dict] = doc.get("files", {})
        self.pending: Dict[str, dict] = doc.get("pending", {})  # rel -> {digest|None, meta}

    # ---------------------------------------------------------------- state
    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        return {"files": {}, "pending": {}}

    def _save(self) -> None:
        doc = {"version": 1, "files": self.state, "pending": self.pending}
        tmp = f"{self.state_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.state_path)

    def _audit(self, events: List[Event], scan_id: str) -> None:
        if not events:
            return
        with open(self.audit_path, "a", encoding="utf-8") as fh:
            for ev in events:
                record = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "scan_id": scan_id,
                    "kind": ev.kind,
                    "path": ev.path,
                    "detail": ev.detail,
                }
                fh.write(json.dumps(record, sort_keys=True) + "\n")

    # ------------------------------------------------------------- admission
    def admit(self, relpath: str, *, operator: str, reason: str, ticket: str = "",
              expected_digest: Optional[str] = None) -> dict:
        """Declare an upcoming legitimate change.

        ``expected_digest`` pins the post-update content; ``None`` admits a
        create/delete or content the operator cannot pre-compute. The
        admission is single-use and consumed on the next matching scan.
        """
        relpath = relpath.replace(os.sep, "/").lstrip("./")
        self.pending[relpath] = {
            "expected_digest": expected_digest,
            "operator": operator,
            "reason": reason,
            "ticket": ticket,
            "admitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._save()
        return dict(self.pending[relpath])

    def _consume_admission(self, rel: str, digest: Optional[str]) -> Optional[dict]:
        entry = self.pending.pop(rel, None)
        if entry is None:
            return None
        expected = entry.get("expected_digest")
        if expected is not None and digest is not None and expected != digest:
            entry["admission_warning"] = "actual digest differs from admitted digest"
        return entry

    # ------------------------------------------------------------- baseline
    def baseline(self) -> None:
        scan_id = f"baseline-{uuid.uuid4().hex[:8]}"
        audit: List[Event] = []
        self.state = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames.sort()
            pruned = []
            kept = []
            for d in dirnames:
                rel = fsutil.rel_posix(self.root, os.path.join(dirpath, d))
                rule = self.rules.match(rel)
                if rule is not None:
                    pruned.append(d)
                    audit.append(Event(RULE_HIT, rel + "/", {"rule": rule.id, "phase": "baseline"}))
                else:
                    kept.append(d)
            dirnames[:] = kept
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = fsutil.rel_posix(self.root, full)
                rule = self.rules.match(rel)
                if rule is not None:
                    audit.append(Event(RULE_HIT, rel, {"rule": rule.id, "phase": "baseline"}))
                    continue
                try:
                    st = os.lstat(full)
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    digest = fsutil.sha256_file(full)
                except OSError as exc:
                    audit.append(Event(SCAN_ERROR, rel, {"error": str(exc)}))
                    continue
                self.state[rel] = {
                    "sha256": digest,
                    "size": st.st_size,
                    "mtime_ns": st.st_mtime_ns,
                    "mode": stat.S_IMODE(st.st_mode),
                    "inode": st.st_ino,
                }
        self.pending = {}
        self._save()
        self._audit(audit, scan_id)

    # ----------------------------------------------------------------- scan
    def scan(self) -> ScanResult:
        scan_id = f"scan-{uuid.uuid4().hex[:8]}"
        result = ScanResult()
        seen = set()

        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames.sort()
            kept = []
            for d in dirnames:
                rel = fsutil.rel_posix(self.root, os.path.join(dirpath, d))
                rule = self.rules.match(rel)
                if rule is not None:
                    result.audit.append(
                        Event(RULE_HIT, rel + "/", {"rule": rule.id, "phase": "scan"})
                    )
                else:
                    kept.append(d)
            dirnames[:] = kept

            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                try:
                    st = os.lstat(full)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                rel = fsutil.rel_posix(self.root, full)
                seen.add(rel)

                rule = self.rules.match(rel)
                if rule is not None:
                    result.audit.append(
                        Event(RULE_HIT, rel, {"rule": rule.id, "phase": "scan"})
                    )
                    self.state.pop(rel, None)
                    self.pending.pop(rel, None)
                    continue

                try:
                    digest = fsutil.sha256_file(full)
                except OSError as exc:
                    result.alerts.append(Event(SCAN_ERROR, rel, {"error": str(exc)}))
                    continue

                mode = stat.S_IMODE(st.st_mode)
                old = self.state.get(rel)

                if old is None:
                    admission = self._consume_admission(rel, digest)
                    if admission is not None:
                        result.audit.append(
                            Event(ADMITTED_UPDATE, rel, {"admission": admission, "sha256": digest})
                        )
                    else:
                        result.alerts.append(
                            Event(UNEXPECTED_CREATED, rel, {"sha256": digest, "size": st.st_size})
                        )
                else:
                    old_digest = old["sha256"]
                    old_mode = old["mode"]
                    inode_changed = old.get("inode") != st.st_ino

                    if digest == old_digest:
                        if inode_changed:
                            result.audit.append(
                                Event(
                                    IDENTICAL_REPLACE,
                                    rel,
                                    {"old_inode": old.get("inode"), "new_inode": st.st_ino},
                                )
                            )
                        if mode != old_mode:
                            ev = self._perms_event(rel, old_mode, mode)
                            (result.alerts if ev.is_alert else result.audit).append(ev)
                    else:
                        admission = self._consume_admission(rel, digest)
                        forged = self._forensics(old, st, digest)
                        if admission is not None:
                            result.audit.append(
                                Event(
                                    ADMITTED_UPDATE,
                                    rel,
                                    {
                                        "admission": admission,
                                        "old_sha256": old_digest,
                                        "new_sha256": digest,
                                        **forged,
                                    },
                                )
                            )
                        else:
                            result.alerts.append(
                                Event(
                                    TAMPER_CONTENT,
                                    rel,
                                    {
                                        "old_sha256": old_digest,
                                        "new_sha256": digest,
                                        **forged,
                                    },
                                )
                            )

                self.state[rel] = {
                    "sha256": digest,
                    "size": st.st_size,
                    "mtime_ns": st.st_mtime_ns,
                    "mode": mode,
                    "inode": st.st_ino,
                }

        for rel in list(self.state):
            if rel not in seen:
                admission = self._consume_admission(rel, None)
                if admission is not None:
                    result.audit.append(Event(ADMITTED_UPDATE, rel, {"admission": admission, "op": "deleted"}))
                else:
                    result.alerts.append(Event(TAMPER_DELETED, rel, {"old_sha256": self.state[rel]["sha256"]}))
                del self.state[rel]

        result.audit.append(
            Event(
                SCAN_INFO,
                ".",
                {"alerts": len(result.alerts), "audit_events": len(result.audit), "scan_id": scan_id},
            )
        )
        self._save()
        self._audit(result.audit + result.alerts, scan_id)
        return result

    @staticmethod
    def _forensics(old: dict, st: os.stat_result, digest: str) -> dict:
        """Highlight attackers who forge size/mtime to cover a hash change."""
        size_same = old.get("size") == st.st_size
        mtime_same = old.get("mtime_ns") == st.st_mtime_ns
        return {
            "size_same": size_same,
            "mtime_same": mtime_same,
            "stat_forged": size_same and mtime_same,
        }

    @staticmethod
    def _perms_event(rel: str, old_mode: int, new_mode: int) -> Event:
        added = (new_mode & ~old_mode) & 0o7777
        dangerous = bool(added & _DANGEROUS_BITS) or bool(new_mode & _WORLD_WRITABLE)
        detail = {"old_mode": f"{old_mode:04o}", "new_mode": f"{new_mode:04o}", "added_bits": f"{added:04o}"}
        if dangerous:
            return Event(TAMPER_PERMS, rel, detail)
        return Event(PERMS_CHANGED, rel, detail)
