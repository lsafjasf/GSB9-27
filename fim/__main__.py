"""Command line entry point.

Examples:
  python3 -m fim --root /etc --state ./fim-state baseline
  python3 -m fim --root /etc --state ./fim-state admit etc/app.conf \
      --operator alice --reason "CR-1001" --ticket CR-1001 \
      --expected-digest <sha256 of the new file>
  python3 -m fim --root /etc --state ./fim-state scan
"""
from __future__ import annotations

import argparse
import json
import sys

from .fmonitor import FixedMonitor
from .fsutil import sha256_file


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m fim")
    ap.add_argument("--root", required=True)
    ap.add_argument("--state", required=True, help="state JSON path")
    ap.add_argument("--rules", default=None, help="rules JSON (default: bundled fim/rules.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("baseline")
    sub.add_parser("scan")
    adm = sub.add_parser("admit")
    adm.add_argument("path")
    adm.add_argument("--operator", required=True)
    adm.add_argument("--reason", required=True)
    adm.add_argument("--ticket", default="")
    g = adm.add_mutually_exclusive_group()
    g.add_argument("--expected-digest", default=None)
    g.add_argument("--expected-file", default=None, help="hash this file to pin content")

    args = ap.parse_args(argv)
    mon = FixedMonitor(args.root, args.state, rules_path=args.rules)

    if args.cmd == "baseline":
        mon.baseline()
        print(json.dumps({"tracked": len(mon.state)}, indent=2))
        return 0

    if args.cmd == "admit":
        digest = args.expected_digest
        if args.expected_file:
            digest = sha256_file(args.expected_file)
        entry = mon.admit(args.path, operator=args.operator, reason=args.reason,
                          ticket=args.ticket, expected_digest=digest)
        print(json.dumps({"admitted": args.path, **entry}, indent=2))
        return 0

    res = mon.scan()
    out = {
        "alerts": [{"kind": e.kind, "path": e.path, "detail": e.detail} for e in res.alerts],
        "audit_events": len(res.audit),
        "audit_log": mon.audit_path,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 1 if res.alerts else 0


if __name__ == "__main__":
    sys.exit(main())
