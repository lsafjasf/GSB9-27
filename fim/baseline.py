"""Pre-fix FIM implementation (the noisy baseline being repaired).

Characteristics of the broken implementation, deliberately reproduced here:

* Content identity is inferred from (size, mtime, mode) only - no hashing.
  Tampering that keeps the size and restores mtime is therefore invisible
  (false negative).
* No exclusion rules: editor temp files, build artifacts and rotated logs
  all raise alerts (false positives).
* Permission-only changes are reported on the same (high-severity) channel
  as content changes.
* No admission/change window for legitimate updates.
* No audit log; alerts cannot be explained after the fact.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import fsutil

# Event ids produced by the baseline.
CREATED = "created"
DELETED = "deleted"
CONTENT_CHANGED = "content_changed"  # fired on size/mtime delta
PERMS_CHANGED = "perms_changed"


@dataclass
class Event:
    kind: str
    path: str
    detail: dict = field(default_factory=dict)


@dataclass
class ScanResult:
    alerts: List[Event] = field(default_factory=list)


class NaiveMonitor:
    """The original monitor: stat-only, no rules, no audit trail."""

    def __init__(self, root: str, state_path: str):
        self.root = os.path.abspath(root)
        self.state_path = state_path
        self.state: Dict[str, list] = {}
        if os.path.exists(state_path):
            with open(state_path, "r", encoding="utf-8") as fh:
                self.state = json.load(fh)

    # -- persistence -------------------------------------------------------
    def _save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
        tmp = f"{self.state_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.state_path)

    def baseline(self) -> None:
        self.state = {}
        for full in fsutil.walk_files(self.root):
            rel = fsutil.rel_posix(self.root, full)
            size, mtime_ns, mode = fsutil.snapshot_stat(full)
            self.state[rel] = [size, mtime_ns, mode]
        self._save()

    # -- scanning ----------------------------------------------------------
    def scan(self) -> ScanResult:
        result = ScanResult()
        seen = set()
        for full in fsutil.walk_files(self.root):
            rel = fsutil.rel_posix(self.root, full)
            seen.add(rel)
            size, mtime_ns, mode = fsutil.snapshot_stat(full)
            old = self.state.get(rel)
            if old is None:
                result.alerts.append(Event(CREATED, rel, {"size": size}))
            else:
                old_size, old_mtime, old_mode = old
                if old_size != size or old_mtime != mtime_ns:
                    result.alerts.append(
                        Event(
                            CONTENT_CHANGED,
                            rel,
                            {
                                "old_size": old_size,
                                "new_size": size,
                                "mtime_changed": old_mtime != mtime_ns,
                            },
                        )
                    )
                elif old_mode != mode:
                    result.alerts.append(
                        Event(PERMS_CHANGED, rel, {"old_mode": f"{old_mode:o}", "new_mode": f"{mode:o}"})
                    )
            self.state[rel] = [size, mtime_ns, mode]

        for rel in list(self.state):
            if rel not in seen:
                result.alerts.append(Event(DELETED, rel))
                del self.state[rel]

        self._save()
        return result
