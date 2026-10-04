"""Command line interface.

  python -m inccheck.cli full   --root REPO
  python -m inccheck.cli inc    --root REPO --changed M:src/a.py D:src/b.py ...
  python -m inccheck.cli inc    --root REPO --changed-from HEAD~1   (needs git)
  python -m inccheck.cli plan   --root REPO --changed M:src/a.py    (dry run)

Exit code: 0 when no check fails, 1 otherwise. The canonical report goes to
stdout; metrics go to stderr (or --metrics-json FILE).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import List

from . import engine
from .model import Change, Status


def _parse_changed(specs: List[str]) -> List[Change]:
    out = []
    for spec in specs:
        status, sep, path = spec.partition(":")
        if not sep or status not in ("A", "M", "D"):
            raise SystemExit(f"bad --changed entry {spec!r}, want A|M|D:path")
        out.append(Change(status, path))
    return out


def _changes_from_git(root: str, ref: str) -> List[Change]:
    proc = subprocess.run(
        ["git", "-C", root, "diff", "--name-status", ref, "--"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"git diff failed: {proc.stderr.strip()}")
    out = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        status, path = parts[0][0], parts[-1]
        out.append(Change(status if status in "AMD" else "M", path))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="inccheck")
    ap.add_argument("command", choices=["full", "inc", "plan"])
    ap.add_argument("--root", required=True)
    ap.add_argument("--changed", nargs="*", default=[])
    ap.add_argument("--changed-from", metavar="GIT_REF")
    ap.add_argument("--metrics-json", metavar="FILE")
    ap.add_argument("--py-root", default="src")
    args = ap.parse_args(argv)

    repo = engine.Repo(args.root, py_root=args.py_root)

    if args.command == "full":
        findings = engine.run_full(repo)
        sys.stdout.write(engine.render_report(findings, args.root))
        return 1 if any(f.status == Status.FAIL for f in findings) else 0

    changes = _parse_changed(args.changed)
    if args.changed_from:
        changes += _changes_from_git(args.root, args.changed_from)

    if args.command == "plan":
        pl = engine.plan(repo, changes, cache_exists=engine.Cache(repo).exists())
        print(f"mode={pl.mode}")
        for reason in pl.fallback_reasons:
            print(f"fallback: {reason}")
        for ch, targets in pl.per_change:
            print(f"{ch.status}:{ch.path} -> {len(targets)} target(s)")
            for t in targets:
                print(f"    {t.check_id} {t.label()}")
        return 0

    result = engine.run_incremental(repo, changes)
    sys.stdout.write(engine.render_report(result.findings, args.root))
    sys.stderr.write(engine.render_metrics(result.metrics))
    if args.metrics_json:
        with open(args.metrics_json, "w", encoding="utf-8") as fh:
            json.dump(result.metrics, fh, indent=1)
    return 1 if any(f.status == Status.FAIL for f in result.findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
