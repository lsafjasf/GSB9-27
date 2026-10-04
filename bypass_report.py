#!/usr/bin/env python3
"""对全部绕过变形输入执行校验，输出判定结果表。

用法: python3 bypass_report.py
若任何一条“应拒绝”的输入被放行（或“应接受”的等价写法被拒绝），
进程以非零状态退出，便于接入 CI。
"""

import sys

from bypass_corpus import CORPUS, reject
from whitelist_validator import ValidationError, validate


def show(value, limit=28):
    text = repr(value)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def collect():
    rows = []
    failures = 0
    for idx, (category, kind, desc, payload, expected) in enumerate(CORPUS, 1):
        try:
            normalized = validate(kind, payload)
            outcome = ("accept", normalized)
            verdict = "接受"
            detail = show(normalized)
        except ValidationError as exc:
            outcome = reject
            verdict = "拒绝"
            detail = str(exc).split(": ", 1)[-1]

        if expected == reject:
            match = outcome == reject
            expected_label = "拒绝"
        else:
            expected_canonical = expected[1]
            match = outcome[0] == "accept" and outcome[1] == expected_canonical
            expected_label = f"接受(={expected_canonical})"
        if not match:
            failures += 1
        rows.append((idx, category, kind, desc, show(payload),
                     expected_label, verdict, detail, "✓" if match else "✗"))
    return rows, failures


def main():
    md = "--md" in sys.argv[1:]
    rows, failures = collect()
    header = ("#", "类别", "规则", "变形说明", "原始输入",
              "期望", "判定", "规范化结果 / 拒绝原因", "结果")
    total = len(CORPUS)
    rejected = sum(1 for *_, e in CORPUS if e == reject)
    accepted = total - rejected
    if md:
        print("| " + " | ".join(header) + " |")
        print("|" + "---|" * len(header))
        for r in rows:
            print("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
        print()
        print(f"变形输入总数: **{total}**（应拒绝 {rejected} 条，"
              f"等价写法应接受 {accepted} 条）。")
        print()
        if failures:
            print(f"**存在 {failures} 条判定与预期不符，校验被绕过！**")
        else:
            print("**全部应拒绝的变形输入均已被拒绝；等价写法均按规范形式接受。**")
        return 1 if failures else 0
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(9)]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(header))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)))
    print()
    print(f"变形输入总数: {total}（应拒绝 {rejected} 条，等价写法应接受 {accepted} 条）")
    if failures:
        print(f"存在 {failures} 条判定与预期不符，校验被绕过！")
        return 1
    print("全部应拒绝的变形输入均已被拒绝；等价写法均按规范形式接受。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
