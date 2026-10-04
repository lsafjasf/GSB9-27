"""端到端演示：多系统流转中的标签合并、继承与丢失检测。

运行：python3 demo.py
"""

from __future__ import annotations

import json

from datatag import (
    CONFIDENTIAL,
    INTERNAL,
    PUBLIC,
    DerivationOp,
    TaggedData,
    UNLABELED,
    Label,
    Level,
    merge_labels,
    pack_envelope,
    record_handoff,
    verify_envelope,
)
from datatag.labels import (
    RESTRICTION_ENCRYPT,
    RESTRICTION_NO_EXPORT,
    RESTRICTION_PII,
    merge_rule_table,
    restriction_rule_table,
)


def show_rule_table() -> None:
    print("=" * 72)
    print("1. 分级合并规则表（6x6，对称；限制标签规则见下）")
    print("=" * 72)
    print(f"{'左标签':<14}{'右标签':<14}{'合并结果':<14}{'冲突':<6}说明")
    for row in merge_rule_table():
        print(
            f"{row['left']:<14}{row['right']:<14}{row['result']:<14}"
            f"{row['conflict']:<6}{row['note']}"
        )
    print()
    print("限制标签合并规则（任一维度独立裁决）：")
    for row in restriction_rule_table():
        print(
            f"  {row['left']:<8} + {row['right']:<8} -> {row['result']:<8}"
            f"  {row['note']}"
        )
    print()


def show_merge_cases() -> None:
    print("=" * 72)
    print("2. 合并场景（同标签 / 冲突 / 含无标签）")
    print("=" * 72)
    cases = [
        ("同标签合并", [CONFIDENTIAL, CONFIDENTIAL]),
        ("分级冲突（只能收紧）", [INTERNAL, CONFIDENTIAL]),
        (
            "限制标签冲突（取并集）",
            [
                Label(Level.CONFIDENTIAL, frozenset({RESTRICTION_PII})),
                Label(Level.CONFIDENTIAL, frozenset({RESTRICTION_NO_EXPORT})),
            ],
        ),
        ("含无标签数据", [UNLABELED, INTERNAL]),
        ("多来源合并", [PUBLIC, INTERNAL, CONFIDENTIAL]),
    ]
    for title, labels in cases:
        result = merge_labels(labels)
        joined = "  ⊕  ".join(str(x) for x in labels)
        print(f"[{title}] {joined}")
        print(f"    => {result.label}   (收紧={result.tightened}, "
              f"含无标签输入={result.unlabeled_input})")
        for conflict in result.conflicts:
            print(
                f"    冲突[{conflict.dimension}]: "
                f"{conflict.left} vs {conflict.right} -> {conflict.resolved}"
            )
    print()


def build_pipeline() -> TaggedData:
    """构造跨系统流转链路：

    crm(客户表, CONFIDENTIAL+PII) ─┐
                                    ├─> etl(合并清洗) ─> bi(报表聚合) ─> export(导出)
    legacy(历史表, 无标签) ────────┘
    """
    crm = TaggedData.source(
        "crm.customers",
        payload={"rows": [{"name": "张三", "phone": "138****0000"}]},
        label=Label(Level.CONFIDENTIAL, frozenset({RESTRICTION_PII})),
        system="crm",
    )
    legacy = TaggedData.source(
        "legacy.orders",
        payload={"rows": [{"order": "A-1", "amount": 99.5}]},
        label=UNLABELED,
        system="legacy",
    )
    merged = TaggedData.derive(
        "etl.customer_orders",
        parents=[crm, legacy],
        transform=DerivationOp(
            system="etl",
            transform="join+cleanse",
            note="客户表与历史订单按客户ID关联",
        ),
        payload={"rows": [{"name": "张三", "amount": 99.5}]},
    )
    report = TaggedData.derive(
        "bi.weekly_report",
        parents=[merged],
        transform=DerivationOp(system="bi", transform="aggregate", note="周报聚合"),
        payload={"week": "2026-W40", "total": 99.5},
    )
    exported = TaggedData.derive(
        "export.weekly_report_csv",
        parents=[report],
        transform=DerivationOp(
            system="export",
            transform="to_csv",
            note=f"导出文件，加密={RESTRICTION_ENCRYPT in report.label.restrictions}",
        ),
        payload="week,total\n2026-W40,99.5\n",
    )
    return exported


def show_lineage(data: TaggedData) -> None:
    print("=" * 72)
    print("3. 派生继承链数据（每个节点继承并收紧来源标签）")
    print("=" * 72)
    for node in data.lineage.chain_to(data.data_id):
        parents = ",".join(node.parents) if node.parents else "(源头)"
        op = f"{node.op.transform}" if node.op else "register"
        print(
            f"  {node.data_id:<26} 系统={node.system:<7} 操作={op:<12} "
            f"来源={parents:<28} 标签={node.label}"
        )
    print()
    problems = data.lineage.audit()
    print(f"继承链审计: {'通过，无放宽' if not problems else problems}")
    print()


def show_transport(data: TaggedData) -> None:
    print("=" * 72)
    print("4. 跨系统交接与下游丢标签检测")
    print("=" * 72)

    print("-- 场景A：下游正常携带标签 --")
    env_ok = pack_envelope(data)
    report = record_handoff(env_ok, "etl", "bi", received_label_raw=env_ok["label"])
    print(f"  交接 etl -> bi: {report}")
    final = verify_envelope(env_ok)
    print(f"  接收侧整体校验: {final}")

    print("-- 场景B：下游丢掉标签 --")
    env_lost = pack_envelope(data)
    report = record_handoff(env_lost, "bi", "export", received_label_raw=None)
    print(f"  交接 bi -> export: {report}")
    final = verify_envelope(env_lost)
    print(f"  接收侧整体校验: {final}")

    print("-- 场景C：下游把标签改宽（CONFIDENTIAL -> INTERNAL）--")
    env_weak = pack_envelope(data)
    weakened = {"level": "INTERNAL", "restrictions": []}
    report = record_handoff(env_weak, "bi", "export", received_label_raw=weakened)
    print(f"  交接 bi -> export: {report}")
    final = verify_envelope(env_weak)
    print(f"  接收侧整体校验: {final}")

    print("-- 场景D：继承链被截断 --")
    env_broken = pack_envelope(data)
    env_broken["lineage"] = env_broken["lineage"][-1:]  # 只留最后一个节点
    final = verify_envelope(env_broken)
    print(f"  接收侧整体校验: {final}")
    print()


def main() -> None:
    show_rule_table()
    show_merge_cases()
    exported = build_pipeline()
    show_lineage(exported)
    show_transport(exported)

    print("=" * 72)
    print("5. 继承链原始数据（JSON，可跨系统传输）")
    print("=" * 72)
    print(exported.lineage.to_json())


if __name__ == "__main__":
    main()
