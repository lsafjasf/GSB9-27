#!/usr/bin/env python3
"""索引迁移辅助工具。

背景：修复后键的等价类会变化（详见 MIGRATION.md），旧索引里若干
原本不同的键会落入同一个新等价类，直接上线会造成“同键多值”，
需要先扫描冲突、确定合并策略，再重建索引。

本工具只读、不改数据：它读入“旧索引的全部键”（每行一个原始键；
若键可能含换行，可用 --json 传 JSON 数组），输出：

  1) 冲突报告：哪些旧键在新规则下会合并，以及合并后的规范键；
  2) 重写映射：旧键 -> 新规范键（JSON），供迁移脚本消费；
  3) 统计：键总数、等价类数、被合并掉的键数。

用法：
    python3 migrate_index.py --keys keys.txt --locale root --report report.txt --map map.json
    python3 migrate_index.py --keys keys.json --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

from keycmp import SUPPORTED_LOCALES, normalize_key


def load_keys(path: str, as_json: bool) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        if as_json:
            data = json.load(handle)
            if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
                raise SystemExit("--json 输入必须是 JSON 字符串数组")
            return data
        return [line.rstrip("\n") for line in handle if line.strip("\n")]


def analyze(keys: list[str], locale: str, strip_marks):
    groups: dict[str, list[str]] = defaultdict(list)
    for key in keys:
        groups[normalize_key(key, locale=locale, strip_marks=strip_marks)].append(key)
    collisions = {canon: members for canon, members in groups.items()
                  if len(set(members)) > 1}
    return groups, collisions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="键等价类迁移扫描")
    parser.add_argument("--keys", required=True, help="旧键列表文件（每行一个，或 JSON 数组）")
    parser.add_argument("--json", action="store_true", help="输入文件为 JSON 字符串数组")
    parser.add_argument("--locale", default="root", choices=SUPPORTED_LOCALES)
    parser.add_argument(
        "--strip-marks",
        default=None,
        metavar="POLICY",
        help="去记号策略：latin（仅拉丁去音符）、all（旧的无条件去记号，"
             "会合并 が/か，不推荐）；缺省不剥离任何记号。",
    )
    parser.add_argument("--report", help="冲突报告输出路径（默认打印到 stdout）")
    parser.add_argument("--map", dest="map_path", help="旧键 -> 新规范键 JSON 映射输出路径")
    args = parser.parse_args(argv)

    keys = load_keys(args.keys, args.json)
    groups, collisions = analyze(keys, args.locale, args.strip_marks)
    merge_map = {key: canon for canon, members in groups.items() for key in members}

    lines = [
        f"区域: {args.locale}",
        f"去记号策略: {args.strip_marks if args.strip_marks is not None else 'off（默认）'}",
        f"旧键总数(含重复): {len(keys)}",
        f"去重后旧键数: {len(set(keys))}",
        f"新等价类数: {len(groups)}",
        f"将被合并的键数(去重): {len(set(keys)) - len(groups)}",
        f"冲突等价类数: {len(collisions)}",
        "",
    ]
    for canon in sorted(collisions):
        lines.append(f"新键 {canon!r} <==")
        for old in sorted(set(collisions[canon])):
            lines.append(f"    {old!r}")
        lines.append("")

    report = "\n".join(lines)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(report)
        print(f"报告已写入 {args.report}")
    else:
        print(report)

    if args.map_path:
        with open(args.map_path, "w", encoding="utf-8") as handle:
            json.dump(merge_map, handle, ensure_ascii=False, indent=2, sort_keys=True)
        print(f"重写映射已写入 {args.map_path}")

    # 有冲突时退出码 2，方便 CI 把“需要人工决定合并策略”卡住
    return 2 if collisions else 0


if __name__ == "__main__":
    raise SystemExit(main())
