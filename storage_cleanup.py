#!/usr/bin/env python3
"""Retention cleanup that never trusts filesystem timestamps.

Root cause of the old bug: the cleaner judged "still in use" by mtime/atime,
but those are controlled by everyone *except* the cleaner -- the archiver
rewrites/touches files, restores reset mtime, reads bump atime.  This module
decides purely on store-owned signals:

  1. refs.json            -- explicit references (who still uses the blob)
  2. archive/<id>.lock    -- in-progress archive task marker (with TTL)
  3. meta/<id>.json       -- store-written creation record (grace period)

Filesystem mtime/atime are only reported as informational fields, never used
for the keep/delete decision.  Anything uncertain (missing refs manifest,
missing meta) is a fail-safe KEEP.

Workflow: `dry-run` writes a plan (candidates + rationale) to journal/;
`execute` re-validates every planned deletion (protecting against concurrent
writers), deletes, and writes an audit log so each removal can be traced back
to its rationale.

Layout under the store root:
  data/<id>.blob    payload
  meta/<id>.json    {"id", "created_at", "size"} written atomically by DataStore
  refs.json         {"refs": {<id>: [owner, ...]}}
  archive/<id>.lock {"task", "started_at"}
  journal/plan-*.jsonl / audit-*.jsonl
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
import time
import uuid
from pathlib import Path

DATA_DIR = "data"
META_DIR = "meta"
ARCHIVE_DIR = "archive"
JOURNAL_DIR = "journal"
REFS_FILE = "refs.json"

DEFAULT_MIN_AGE = 3600.0        # grace period for freshly written data
DEFAULT_LOCK_TTL = 24 * 3600.0  # archive locks older than this are stale


class StorageFullError(OSError):
    """Raised when a write fails because the disk is full (ENOSPC)."""


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write payload to path atomically (tmp file + rename), fsync'd."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        if exc.errno == errno.ENOSPC:
            raise StorageFullError(f"disk full writing {path}: {exc}") from exc
        raise


class DataStore:
    def __init__(self, root):
        self.root = Path(root)
        for name in (DATA_DIR, META_DIR, ARCHIVE_DIR, JOURNAL_DIR):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    # -- blobs -----------------------------------------------------------
    def blob_path(self, data_id: str) -> Path:
        return self.root / DATA_DIR / f"{data_id}.blob"

    def meta_path(self, data_id: str) -> Path:
        return self.root / META_DIR / f"{data_id}.json"

    def write(self, data_id: str, payload: bytes, now: float | None = None) -> dict:
        """Write a blob plus its creation record. `now` is injectable for tests."""
        now = time.time() if now is None else now
        _atomic_write_bytes(self.blob_path(data_id), payload)
        meta = {"id": data_id, "created_at": now, "size": len(payload)}
        _atomic_write_bytes(self.meta_path(data_id), json.dumps(meta).encode())
        return meta

    def read(self, data_id: str) -> bytes:
        return self.blob_path(data_id).read_bytes()

    def list_blobs(self) -> list[str]:
        data = self.root / DATA_DIR
        return sorted(p.name[: -len(".blob")] for p in data.glob("*.blob"))

    # -- references --------------------------------------------------------
    def refs_path(self) -> Path:
        return self.root / REFS_FILE

    def load_refs(self) -> dict | None:
        """Return {id: [owners]} or None if the manifest is missing."""
        path = self.refs_path()
        if not path.exists():
            return None
        return json.loads(path.read_text()).get("refs", {})

    def _save_refs(self, refs: dict) -> None:
        _atomic_write_bytes(self.refs_path(), json.dumps({"refs": refs}, indent=2).encode())

    def add_ref(self, data_id: str, owner: str) -> None:
        refs = self.load_refs() or {}
        owners = refs.setdefault(data_id, [])
        if owner not in owners:
            owners.append(owner)
        self._save_refs(refs)

    def remove_ref(self, data_id: str, owner: str) -> None:
        refs = self.load_refs() or {}
        owners = refs.get(data_id, [])
        if owner in owners:
            owners.remove(owner)
        if not owners:
            refs.pop(data_id, None)
        self._save_refs(refs)

    # -- archive task markers ---------------------------------------------
    def lock_path(self, data_id: str) -> Path:
        return self.root / ARCHIVE_DIR / f"{data_id}.lock"

    def start_archive(self, data_id: str, task: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        _atomic_write_bytes(
            self.lock_path(data_id),
            json.dumps({"id": data_id, "task": task, "started_at": now}).encode(),
        )

    def finish_archive(self, data_id: str) -> None:
        self.lock_path(data_id).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Policy evaluation
# ---------------------------------------------------------------------------

def _decision(store: DataStore, data_id: str, action: str, reason: str, detail: str,
              st: os.stat_result, **extra) -> dict:
    entry = {
        "type": "decision",
        "id": data_id,
        "path": str(store.blob_path(data_id).relative_to(store.root)),
        "action": action,
        "reason": reason,
        "detail": detail,
        # identity snapshot so execute() can detect concurrent replacement
        "inode": st.st_ino,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        # informational only -- never used for the decision
        "mtime": st.st_mtime,
        "atime": st.st_atime,
    }
    entry.update(extra)
    return entry


def evaluate_blob(store: DataStore, data_id: str, now: float, min_age: float,
                  lock_ttl: float, refs: dict | None) -> dict:
    """Decide keep/delete for one blob. Never consults mtime/atime."""
    st = store.blob_path(data_id).stat()

    if refs is None:
        return _decision(store, data_id, "keep", "KEEP_REFS_MISSING",
                         "refs.json missing; fail-safe keep (cannot prove unreferenced)", st)

    owners = refs.get(data_id) or []
    if owners:
        return _decision(store, data_id, "keep", "KEEP_REFERENCED",
                         f"referenced by {', '.join(owners)}", st, referenced_by=owners)

    lock_file = store.lock_path(data_id)
    if lock_file.exists():
        try:
            lock = json.loads(lock_file.read_text())
            lock_age = now - float(lock.get("started_at", 0))
        except (ValueError, TypeError):
            lock, lock_age = None, float("inf")
        if lock_age <= lock_ttl:
            task = lock.get("task", "unknown") if lock else "unreadable-lock"
            return _decision(store, data_id, "keep", "KEEP_ARCHIVE_ACTIVE",
                             f"archive task '{task}' in progress (lock age {lock_age:.0f}s)",
                             st, archive_task=task)
        # stale lock: ignored, noted in the final detail

    meta_file = store.meta_path(data_id)
    if not meta_file.exists():
        return _decision(store, data_id, "keep", "KEEP_META_MISSING",
                         "creation record missing; fail-safe keep (cannot establish age)", st)
    meta = json.loads(meta_file.read_text())
    age = now - float(meta["created_at"])
    if age < min_age:
        return _decision(store, data_id, "keep", "KEEP_WITHIN_GRACE",
                         f"age {age:.0f}s < min_age {min_age:.0f}s (recently written)",
                         st, age_seconds=age)
    return _decision(store, data_id, "delete", "DELETE_ELIGIBLE",
                     f"unreferenced; no active archive; age {age:.0f}s >= min_age {min_age:.0f}s",
                     st, age_seconds=age)


def evaluate_all(store: DataStore, now: float, min_age: float,
                 lock_ttl: float = DEFAULT_LOCK_TTL) -> list[dict]:
    refs = store.load_refs()
    return [evaluate_blob(store, data_id, now, min_age, lock_ttl, refs)
            for data_id in store.list_blobs()]


# ---------------------------------------------------------------------------
# Dry-run plan and audited execution
# ---------------------------------------------------------------------------

def _journal_path(store: DataStore, kind: str, now: float) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now))
    return store.root / JOURNAL_DIR / f"{kind}-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}.jsonl"


def dry_run(store: DataStore, now: float | None = None, min_age: float = DEFAULT_MIN_AGE,
            lock_ttl: float = DEFAULT_LOCK_TTL) -> tuple[Path, list[dict]]:
    """Evaluate all blobs and write a plan file. Deletes nothing."""
    now = time.time() if now is None else now
    decisions = evaluate_all(store, now, min_age, lock_ttl)
    plan_path = _journal_path(store, "plan", now)
    header = {
        "type": "header",
        "created_at": now,
        "min_age": min_age,
        "lock_ttl": lock_ttl,
        "candidates": sum(1 for d in decisions if d["action"] == "delete"),
        "kept": sum(1 for d in decisions if d["action"] == "keep"),
    }
    with open(plan_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(header) + "\n")
        for d in decisions:
            fh.write(json.dumps(d) + "\n")
    return plan_path, decisions


def execute(store: DataStore, plan_path, now: float | None = None,
            lock_ttl: float = DEFAULT_LOCK_TTL) -> tuple[Path, list[dict]]:
    """Execute a dry-run plan, re-validating every entry before deleting.

    Returns (audit_path, audit_entries). Each deletion/skipped/error entry
    carries the original plan rationale plus the re-validation result, so
    every outcome is traceable.
    """
    now = time.time() if now is None else now
    plan_path = Path(plan_path)
    lines = [json.loads(l) for l in plan_path.read_text().splitlines() if l.strip()]
    header = next((l for l in lines if l.get("type") == "header"), {})
    min_age = float(header.get("min_age", DEFAULT_MIN_AGE))

    audit: list[dict] = []
    for entry in lines:
        if entry.get("type") != "decision" or entry.get("action") != "delete":
            continue
        data_id = entry["id"]
        record = {"type": "audit", "id": data_id, "plan": plan_path.name,
                  "plan_reason": entry["reason"], "plan_detail": entry["detail"]}
        blob = store.blob_path(data_id)
        try:
            st = blob.stat()
        except FileNotFoundError:
            record.update(outcome="skipped", recheck="gone",
                          detail="file already removed before execution")
            audit.append(record)
            continue
        # Identity check: a concurrent writer may have replaced the file
        # (atomic rename -> new inode) after the plan was made.
        if (st.st_ino, st.st_size, st.st_mtime_ns) != (
                entry["inode"], entry["size"], entry["mtime_ns"]):
            record.update(outcome="skipped", recheck="changed",
                          detail="file changed since dry-run (concurrent write); not deleting")
            audit.append(record)
            continue
        # Policy re-check with fresh refs/locks/meta.
        refs = store.load_refs()
        current = evaluate_blob(store, data_id, now, min_age, lock_ttl, refs)
        record["recheck_reason"] = current["reason"]
        if current["action"] != "delete":
            record.update(outcome="skipped", recheck="policy",
                          detail=f"re-validation says keep: {current['detail']}")
            audit.append(record)
            continue
        try:
            blob.unlink()
            store.meta_path(data_id).unlink(missing_ok=True)
            record.update(outcome="deleted", recheck="ok",
                          detail=current["detail"])
        except OSError as exc:
            record.update(outcome="error", recheck="ok",
                          detail=f"unlink failed: {exc}")
        audit.append(record)

    audit_path = _journal_path(store, "audit", now)
    with open(audit_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "header", "plan": plan_path.name,
                             "executed_at": now}) + "\n")
        for record in audit:
            fh.write(json.dumps(record) + "\n")
    return audit_path, audit


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_decisions(decisions: list[dict]) -> None:
    for d in decisions:
        print(f"{d['action'].upper():6} {d['id']:20} {d['reason']:22} {d['detail']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("dry-run", "execute"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True, help="store root directory")
        p.add_argument("--min-age", type=float, default=DEFAULT_MIN_AGE)
        p.add_argument("--lock-ttl", type=float, default=DEFAULT_LOCK_TTL)
        p.add_argument("--now", type=float, default=None,
                       help="override current time (testing/repro)")
    sub.choices["execute"].add_argument("--plan", required=True,
                                        help="plan file produced by dry-run")
    args = parser.parse_args(argv)

    store = DataStore(args.root)
    if args.command == "dry-run":
        plan_path, decisions = dry_run(store, now=args.now, min_age=args.min_age,
                                       lock_ttl=args.lock_ttl)
        _print_decisions(decisions)
        deletes = sum(1 for d in decisions if d["action"] == "delete")
        print(f"\nplan: {plan_path}  ({deletes} delete candidate(s), "
              f"{len(decisions) - deletes} kept)")
        return 0

    audit_path, audit = execute(store, args.plan, now=args.now, lock_ttl=args.lock_ttl)
    for a in audit:
        print(f"{a['outcome'].upper():8} {a['id']:20} plan={a['plan_reason']:16} "
              f"recheck={a.get('recheck_reason', a['recheck']):16} {a['detail']}")
    errors = sum(1 for a in audit if a["outcome"] == "error")
    print(f"\naudit: {audit_path}  ({len(audit)} entr(ies), {errors} error(s))")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
