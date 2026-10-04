"""命令行入口：

  python -m ifacecheck compare OLD.json NEW.json
  python -m ifacecheck check OLD.json NEW.json \
      --impl-old impl_v1.py --impl-new impl_v2.py --cases cases.json
"""
from __future__ import annotations

import argparse
import sys

from .compare import compare_interfaces
from .model import Interface
from .report import render_full, render_judgment_table
from .runtime import check_consistency, load_cases, load_impl, run_cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ifacecheck")
    sub = parser.add_subparsers(dest="command", required=True)

    p_cmp = sub.add_parser("compare", help="仅做静态接口对比")
    p_cmp.add_argument("old")
    p_cmp.add_argument("new")

    p_chk = sub.add_parser("check", help="静态对比 + 实际调用对照")
    p_chk.add_argument("old")
    p_chk.add_argument("new")
    p_chk.add_argument("--impl-old", required=True)
    p_chk.add_argument("--impl-new", required=True)
    p_chk.add_argument("--cases", required=True)

    args = parser.parse_args(argv)
    old = Interface.from_json_file(args.old)
    new = Interface.from_json_file(args.new)
    report = compare_interfaces(old, new)

    if args.command == "compare":
        print(render_judgment_table(report))
        return 0

    cases = load_cases(args.cases)
    old_impl = load_impl(args.impl_old, "ifacecheck_impl_old")
    new_impl = load_impl(args.impl_new, "ifacecheck_impl_new")
    results = run_cases(cases, old_impl, new_impl)
    inconsistencies = check_consistency(report, results)
    print(render_full(report, results, inconsistencies))
    return 0


if __name__ == "__main__":
    sys.exit(main())
