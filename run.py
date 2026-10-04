#!/usr/bin/env python3
"""数据分类分级 CLI。

用法：
    python3 run.py [--fields data/fields.json] [--reviewed data/reviewed.json]
                   [--report-dir reports]

输出：
    1. 分级依据表（Markdown）：每个字段的级别、命中规则、证据与冲突裁决。
    2. 对拍报告（Markdown）：与人工复核标签的比对结果及不一致清单。
"""
import argparse
import json
import sys
from pathlib import Path

from dataclassify import Classifier
from dataclassify.levels import LEVELS


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def render_evidence_table(results) -> str:
    lines = [
        "# 分级依据表",
        "",
        f"级别定义：{'；'.join(f'{k}={v}' for k, v in LEVELS.items())}",
        "",
        "| 字段 | 级别 | 命中规则（按裁决顺序） | 证据 | 冲突裁决 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for res in results:
        if res.evidences:
            rules = "<br>".join(
                f"{e.rule_id}→{e.level}(P{e.priority})" for e in res.evidences
            )
            evidence = "<br>".join(
                f"[{e.rule_id}] {e.detail}（依据：{e.basis}）" for e in res.evidences
            )
        else:
            rules = "（无）"
            evidence = res.reason
        if res.conflicts:
            conflicts = "<br>".join(
                f"{c.winner_rule}({c.winner_level}) 胜过 {c.loser_rule}({c.loser_level})：{c.reason}"
                for c in res.conflicts
            )
        else:
            conflicts = "无"
        lines.append(
            f"| {res.field_name} | {res.final_level} {res.final_level_name} "
            f"| {rules} | {evidence} | {conflicts} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_diff_report(rows, mismatches) -> str:
    lines = [
        "# 对拍报告（自动分级 vs 人工复核）",
        "",
        "| 字段 | 自动分级 | 人工复核 | 是否一致 | 复核备注 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        mark = "✅" if row["match"] else "❌"
        lines.append(
            f"| {row['field']} | {row['auto']} | {row['human']} | {mark} | {row['note']} |"
        )
    lines.append("")
    lines.append(f"共 {len(rows)} 条，一致 {len(rows) - len(mismatches)} 条，不一致 {len(mismatches)} 条。")
    if mismatches:
        lines.append("")
        lines.append("## 不一致清单")
        lines.append("")
        for item in mismatches:
            lines.append(
                f"- `{item['field']}`：自动={item['auto']}，人工={item['human']}（{item['note']}）"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="数据分类分级与对拍")
    parser.add_argument("--fields", default="data/fields.json")
    parser.add_argument("--reviewed", default="data/reviewed.json")
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args(argv)

    fields = load_json(args.fields)["fields"]
    reviewed = load_json(args.reviewed)["reviewed"]

    classifier = Classifier()
    results = []
    by_id = {}
    for item in fields:
        res = classifier.classify(
            item["name"], samples=item.get("samples"), source=item.get("source", "")
        )
        results.append(res)
        by_id[item["id"]] = (item, res)

    rows, mismatches = [], []
    for field_id, (item, res) in by_id.items():
        human = reviewed.get(field_id)
        human_level = human["level"] if human else "（未复核）"
        note = human["note"] if human else ""
        match = human is not None and human_level == res.final_level
        row = {
            "field": item["name"],
            "auto": f"{res.final_level} {res.final_level_name}",
            "human": human_level,
            "match": match,
            "note": note,
        }
        rows.append(row)
        if not match:
            mismatches.append(row)

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "evidence_table.md").write_text(
        render_evidence_table(results), encoding="utf-8"
    )
    (report_dir / "diff_report.md").write_text(
        render_diff_report(rows, mismatches), encoding="utf-8"
    )

    for row in rows:
        mark = "OK " if row["match"] else "DIFF"
        print(f"[{mark}] {row['field']:<20} 自动={row['auto']:<12} 人工={row['human']}")
    print(f"\n对拍：{len(rows) - len(mismatches)}/{len(rows)} 一致；"
          f"报告已写入 {report_dir}/evidence_table.md 与 {report_dir}/diff_report.md")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
