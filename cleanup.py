#!/usr/bin/env python3
"""Retention cleanup for blob data (fixed implementation).

Eligibility is decided ONLY by explicit state, never by timestamp inference:

  1. the blob is NOT referenced (refs.json);
  2. the blob is NOT part of an in-progress archive (archive_state.json);
  3. the blob's archive HAS completed (archive_state.json);
  4. the blob's mtime is older than the retention window (not just written).

atime is never consulted: the archive task reads blobs and distorts it.

Workflow: scan + dry-run report (candidates with per-criterion verdicts),
then --execute deletes and appends one audit record per deletion to
cleanup_audit.jsonl so every deleted blob's criteria can be traced later.
If the audit log cannot be written, nothing is deleted (fail-safe).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass

REFS_FILE = "refs.json"
ARCHIVE_STATE_FILE = "archive_state.json"
AUDIT_FILE = "cleanup_audit.jsonl"
META_FILES = {REFS_FILE, ARCHIVE_STATE_FILE, AUDIT_FILE}

DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600


class CleanupError(Exception):
    """Fatal, fail-safe error: nothing has been or will be deleted."""


@dataclass
class Decision:
    blob_id: str
    path: str
    eligible: bool
    criteria: dict
    mtime_ns: int

    def as_dict(self):
        return {
            "blob_id": self.blob_id,
            "path": self.path,
            "eligible": self.eligible,
            "criteria": self.criteria,
        }


def _load_json(path, context):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise CleanupError(f"{context}: corrupt JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise CleanupError(f"{context}: cannot read {path}: {exc}") from exc


def load_state(data_dir):
    """Return (referenced, archive_in_progress, archive_completed) id sets."""
    refs_path = os.path.join(data_dir, REFS_FILE)
    if not os.path.exists(refs_path):
        # Fail-safe: without reference info we cannot prove anything is
        # unreferenced, so nothing may be deleted.
        raise CleanupError(
            f"reference info missing: {refs_path}; refusing to delete anything"
        )
    referenced = set(_load_json(refs_path, "reference info"))

    archive_path = os.path.join(data_dir, ARCHIVE_STATE_FILE)
    if os.path.exists(archive_path):
        state = _load_json(archive_path, "archive state")
    else:
        # No archive record => no blob is proven archived => none eligible.
        state = {}
    in_progress = set(state.get("in_progress", []))
    completed = set(state.get("completed", []))
    return referenced, in_progress, completed


def evaluate_blob(blob_id, path, st, referenced, in_progress, completed,
                  now, retention_seconds):
    age = now - st.st_mtime
    checks = {
        "referenced": {
            "actual": blob_id in referenced,
            "required": False,
        },
        "archive_in_progress": {
            "actual": blob_id in in_progress,
            "required": False,
        },
        "archive_completed": {
            "actual": blob_id in completed,
            "required": True,
        },
        "age_seconds": {
            "actual": round(age, 3),
            "required": f">= {retention_seconds}",
        },
    }
    ok = (
        checks["referenced"]["actual"] is False
        and checks["archive_in_progress"]["actual"] is False
        and checks["archive_completed"]["actual"] is True
        and age >= retention_seconds
    )
    for name, passed in (
        ("referenced", checks["referenced"]["actual"] is False),
        ("archive_in_progress", checks["archive_in_progress"]["actual"] is False),
        ("archive_completed", checks["archive_completed"]["actual"] is True),
        ("age_seconds", age >= retention_seconds),
    ):
        checks[name]["ok"] = passed
    return Decision(blob_id, path, ok, checks, st.st_mtime_ns)


def scan(data_dir, retention_seconds, now=None):
    now = time.time() if now is None else now
    referenced, in_progress, completed = load_state(data_dir)
    decisions = []
    for name in sorted(os.listdir(data_dir)):
        if name in META_FILES:
            continue
        path = os.path.join(data_dir, name)
        if not os.path.isfile(path):
            continue
        st = os.stat(path)
        decisions.append(
            evaluate_blob(name, path, st, referenced, in_progress, completed,
                          now, retention_seconds)
        )
    return now, decisions


def dry_run_report(data_dir, decisions, now, retention_seconds):
    candidates = [d for d in decisions if d.eligible]
    excluded = [d for d in decisions if not d.eligible]
    return {
        "mode": "dry-run",
        "data_dir": os.path.abspath(data_dir),
        "now": now,
        "retention_seconds": retention_seconds,
        "candidates": [d.as_dict() for d in candidates],
        "excluded": [d.as_dict() for d in excluded],
        "summary": {
            "scanned": len(decisions),
            "candidates": len(candidates),
            "excluded": len(excluded),
        },
    }


def _open_audit(data_dir):
    path = os.path.join(data_dir, AUDIT_FILE)
    return open(path, "a", encoding="utf-8")


def _audit_write(fh, record):
    fh.write(json.dumps(record, sort_keys=True) + "\n")
    fh.flush()
    os.fsync(fh.fileno())


def execute(data_dir, decisions, now, retention_seconds, before_delete=None):
    """Delete eligible blobs. Every deletion is audited with its criteria.

    ``before_delete`` is a test hook invoked with each path just before its
    pre-delete re-check, to simulate concurrent writers.
    """
    try:
        audit = _open_audit(data_dir)
    except OSError as exc:
        # No audit trail => no deletions. Traceability is mandatory.
        raise CleanupError(
            f"cannot open audit log ({exc}); aborting, nothing deleted"
        ) from exc

    deleted, skipped, failed = [], [], []
    with audit:
        _audit_write(audit, {
            "event": "run_start",
            "now": now,
            "retention_seconds": retention_seconds,
        })
        for decision in decisions:
            if not decision.eligible:
                continue
            if before_delete is not None:
                before_delete(decision.path)
            try:
                st = os.stat(decision.path)
            except FileNotFoundError:
                skipped.append((decision, "vanished_before_delete"))
                continue
            if st.st_mtime_ns != decision.mtime_ns:
                # Concurrent writer touched it since the scan: re-check fails.
                skipped.append((decision, "modified_since_scan"))
                _audit_write(audit, {
                    "event": "skipped",
                    "blob_id": decision.blob_id,
                    "path": decision.path,
                    "reason": "modified_since_scan",
                })
                continue
            try:
                os.remove(decision.path)
            except OSError as exc:
                failed.append((decision, str(exc)))
                _audit_write(audit, {
                    "event": "delete_failed",
                    "blob_id": decision.blob_id,
                    "path": decision.path,
                    "error": str(exc),
                })
                continue
            record = {
                "event": "deleted",
                "blob_id": decision.blob_id,
                "path": decision.path,
                "deleted_at": time.time(),
                "criteria": decision.criteria,
            }
            _audit_write(audit, record)
            deleted.append(decision)
        _audit_write(audit, {
            "event": "run_end",
            "deleted": len(deleted),
            "skipped": len(skipped),
            "failed": len(failed),
        })
    return {
        "deleted": [d.blob_id for d in deleted],
        "skipped": [{"blob_id": d.blob_id, "reason": r} for d, r in skipped],
        "failed": [{"blob_id": d.blob_id, "error": e} for d, e in failed],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir")
    parser.add_argument("--retention-seconds", type=float,
                        default=DEFAULT_RETENTION_SECONDS)
    parser.add_argument("--execute", action="store_true",
                        help="actually delete (default is dry-run only)")
    parser.add_argument("--now", type=float, default=None,
                        help="override current time (testing)")
    args = parser.parse_args(argv)

    try:
        now, decisions = scan(args.data_dir, args.retention_seconds, now=args.now)
    except CleanupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = dry_run_report(args.data_dir, decisions, now,
                            args.retention_seconds)
    if not args.execute:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    report["mode"] = "execute"
    try:
        report["result"] = execute(args.data_dir, decisions, now,
                                   args.retention_seconds)
    except CleanupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["result"]["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
