#!/usr/bin/env python3
"""迁移 / 降级命令行工具，输出可逐条核对的迁移报告与往返差异。

用法:
  python3 migrate_tool.py upgrade   in.json --from 1 --to 3 -o out.json --report report.json
  python3 migrate_tool.py downgrade in.json --from 3 --to 1 -o out.json
  python3 migrate_tool.py roundtrip in.json --from 1 --via 3

所有子命令把人类可读的摘要打印到 stdout，JSON 结果写入 -o/--report 指定的文件。
"""

import argparse
import json
import sys

from dataformat import (
    MigrationError,
    build_report,
    downgrade,
    roundtrip_diff,
    upgrade,
    verify_report,
)


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(data, path):
    if path is None:
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _print_report(report):
    print(f"迁移报告: v{report['from_version']} -> v{report['to_version']}")
    for entry in report["fields"]:
        action = entry["action"]
        path = entry["path"]
        if action == "renamed":
            print(f"  [renamed]           {path} -> {entry['to']}"
                  f" ({entry['transform']}): {entry['value_before']!r} -> {entry['value_after']!r}")
        elif action == "removed":
            print(f"  [removed]           {path}: {entry['value_before']!r}"
                  f" (归档到 {entry['archived_to']}，降级可恢复)")
        elif action == "defaulted":
            print(f"  [defaulted]         {path} = {entry['value_after']!r} (来源: schema 默认值)")
        elif action == "kept":
            print(f"  [kept]              {path} = {entry['value_after']!r}")
        elif action == "preserved_unknown":
            print(f"  [preserved_unknown] {path} = {entry['value_after']!r} (未知字段，原样保留)")
        elif action == "version_bump":
            print(f"  [version_bump]      version: {entry['value_before']} -> {entry['value_after']}")
        else:
            print(f"  [{action}] {path}: {entry}")
    summary = {k: v for k, v in report["summary"].items() if v}
    print(f"汇总: {summary}")


def cmd_upgrade(args):
    data = _load(args.input)
    result = upgrade(data, args.from_v, args.to_v)
    report = build_report(data, args.from_v, args.to_v, result=result)
    verify_report(report, data, result)
    _print_report(report)
    print("报告核对: 通过（每条目均与迁移结果一致）")
    _dump(result, args.output)
    _dump(report, args.report)
    return 0


def cmd_downgrade(args):
    data = _load(args.input)
    result = downgrade(data, args.from_v, args.to_v)
    print(f"降级完成: v{args.from_v} -> v{args.to_v}")
    _dump(result, args.output)
    return 0


def cmd_roundtrip(args):
    data = _load(args.input)
    back, diffs = roundtrip_diff(data, args.from_v, args.via)
    print(f"往返: v{args.from_v} -> v{args.via} -> v{args.from_v}")
    if not diffs:
        print("往返差异: 无（与原文档严格相等）")
    else:
        print(f"往返差异: {len(diffs)} 处（往返结果是原文档的超集，非严格相等）")
        for diff in diffs:
            if diff["kind"] == "added":
                print(f"  [added]   {diff['path']} = {diff['roundtrip_value']!r} -- {diff['reason']}")
            elif diff["kind"] == "changed":
                print(f"  [changed] {diff['path']}: {diff['original_value']!r} -> {diff['roundtrip_value']!r}")
            else:
                print(f"  [removed] {diff['path']} = {diff['original_value']!r}")
    _dump(back, args.output)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("upgrade", help="升级并生成迁移报告")
    p_up.add_argument("input")
    p_up.add_argument("--from", dest="from_v", type=int, required=True)
    p_up.add_argument("--to", dest="to_v", type=int, required=True)
    p_up.add_argument("-o", "--output")
    p_up.add_argument("--report", help="迁移报告 JSON 输出路径")
    p_up.set_defaults(func=cmd_upgrade)

    p_down = sub.add_parser("downgrade", help="降级（回退），供旧代码读取")
    p_down.add_argument("input")
    p_down.add_argument("--from", dest="from_v", type=int, required=True)
    p_down.add_argument("--to", dest="to_v", type=int, required=True)
    p_down.add_argument("-o", "--output")
    p_down.set_defaults(func=cmd_downgrade)

    p_rt = sub.add_parser("roundtrip", help="升级再降级，列出往返差异")
    p_rt.add_argument("input")
    p_rt.add_argument("--from", dest="from_v", type=int, required=True)
    p_rt.add_argument("--via", type=int, required=True)
    p_rt.add_argument("-o", "--output")
    p_rt.set_defaults(func=cmd_roundtrip)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except MigrationError as exc:
        print(f"迁移失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
