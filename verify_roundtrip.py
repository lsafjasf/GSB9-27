#!/usr/bin/env python3
"""可复跑的端到端验证：迁移报告 + 降级回退 + 往返一致性。

流程:
  1. v1 样例文档升级到 v3，生成逐字段迁移报告；
  2. verify_report 对照输入与迁移结果逐条复核报告；
  3. v3 降级回 v1（回退工具），校验关键字段还原；
  4. roundtrip_diff 列出往返差异，断言差异仅为「超集」方向；
  5. 全部真实输出写入 out/ 目录，可逐条人工核对。

运行: python3 verify_roundtrip.py   （任何一步失败即非零退出）
"""

import json
import os
import sys

from dataformat import (
    build_report,
    downgrade,
    roundtrip_diff,
    upgrade,
    verify_report,
)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

SAMPLE_V1 = {
    "version": 1,
    "name": "order-service",
    "timeout": 30,                       # v1 语义: 秒
    "server": {"host": "db.internal"},   # port 缺失 -> 默认值填充
    "owner": "team-pay",                 # 未知字段 -> 原样保留
}


def _write(name, data):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  已写出 {path}")
    return path


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("== 1. 升级 v1 -> v3 并生成迁移报告 ==")
    result_v3 = upgrade(SAMPLE_V1, 1, 3)
    report = build_report(SAMPLE_V1, 1, 3, result=result_v3)
    _write("upgraded_v3.json", result_v3)
    _write("report_v1_to_v3.json", report)
    for entry in report["fields"]:
        print(f"  [{entry['action']}] {entry['path']}")

    print("\n== 2. 逐条核对报告与迁移结果 ==")
    verify_report(report, SAMPLE_V1, result_v3)
    print(f"  核对通过: {len(report['fields'])} 条报告条目均与迁移结果一致")

    print("\n== 3. 降级回退 v3 -> v1 ==")
    back_v1 = downgrade(result_v3, 3, 1)
    _write("downgraded_back_v1.json", back_v1)
    assert back_v1["name"] == SAMPLE_V1["name"], "name 应从归档恢复"
    assert back_v1["timeout"] == SAMPLE_V1["timeout"], "timeout 应逆变换还原"
    assert back_v1["owner"] == SAMPLE_V1["owner"], "未知字段应保留"
    print("  关键字段还原: name / timeout / owner 均与原文档一致")

    print("\n== 4. 往返一致性（升级为超集，非严格相等） ==")
    back, diffs = roundtrip_diff(SAMPLE_V1, 1, 3)
    _write("roundtrip_diff.json", diffs)
    for diff in diffs:
        print(f"  [{diff['kind']}] {diff['path']} = {diff.get('roundtrip_value')!r}")
    kinds = {d["kind"] for d in diffs}
    assert kinds <= {"added"}, f"往返只允许超集方向差异，实际: {kinds}"
    added = {d["path"] for d in diffs}
    assert added == {"server.port", "server.retries", "tags"}, added
    print("  差异仅为新增字段（超集）: server.port / server.retries / tags")
    print("  原因: 这些是较新版本引入的默认值，降级时按未知字段策略保留，")
    print("        保证不丢数据，因此往返结果是原文档的超集而非严格相等。")

    print("\n全部验证通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
