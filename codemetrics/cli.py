"""Command line interface:  python -m codemetrics <path> [options]"""
from __future__ import annotations

import argparse
import os
import sys
import time

from .complexity import analyze_file
from .coupling import analyze_tree


def _iter_py_files(path):
    if os.path.isfile(path):
        yield path
        return
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "__"))]
        for fname in sorted(filenames):
            if fname.endswith(".py"):
                yield os.path.join(dirpath, fname)


def run_complexity(path, top, min_complexity):
    rows = []
    n_files = n_loc = 0
    skipped = 0
    for fpath in _iter_py_files(path):
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                source = fh.read()
            result = analyze_file(fpath)
        except (SyntaxError, UnicodeDecodeError):
            skipped += 1
            continue
        n_files += 1
        n_loc += source.count("\n") + 1
        for fn in result.functions:
            if fn.complexity >= min_complexity:
                rows.append((fn.complexity, fn.loc, fn.qualname, fpath, fn.lineno))
    rows.sort(key=lambda r: (-r[0], -r[1]))
    print(f"# files={n_files} loc={n_loc} functions={len(rows)} skipped={skipped}")
    print(f"{'CPLX':>5} {'LOC':>6}  {'FUNCTION':<50} LOCATION")
    for complexity, loc, qual, fpath, lineno in rows[:top]:
        print(f"{complexity:>5} {loc:>6}  {qual:<50} {fpath}:{lineno}")


def run_coupling(path, top):
    couplings = analyze_tree(path)
    rows = sorted(couplings.values(), key=lambda c: (-(c.ca + c.ce), c.module))
    print(f"# modules={len(rows)}")
    print(f"{'CA':>4} {'CE':>4} {'I':>6}  MODULE")
    for c in rows[:top]:
        print(f"{c.ca:>4} {c.ce:>4} {c.instability:>6.3f}  {c.module}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="codemetrics",
        description="Function complexity + module coupling metrics (stdlib only).",
    )
    parser.add_argument("path", help="Python file or directory to analyze")
    parser.add_argument("--top", type=int, default=20,
                        help="rows to print per report (default 20)")
    parser.add_argument("--min-complexity", type=int, default=1,
                        help="only show functions at or above this complexity")
    parser.add_argument("--no-coupling", action="store_true",
                        help="skip the module coupling report")
    parser.add_argument("--no-complexity", action="store_true",
                        help="skip the function complexity report")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    if not args.no_complexity:
        print("== Function complexity (highest first) ==")
        run_complexity(args.path, args.top, args.min_complexity)
        print()
    if not args.no_coupling and os.path.isdir(args.path):
        print("== Module coupling (Ca=afferent/incoming, Ce=efferent/outgoing, "
              "I=Ce/(Ca+Ce)) ==")
        run_coupling(args.path, args.top)
        print()
    elapsed = time.perf_counter() - started
    print(f"# elapsed: {elapsed:.3f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
