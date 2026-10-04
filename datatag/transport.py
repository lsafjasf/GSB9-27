"""跨系统传输信封：标签与数据绑定流转，下游丢标签可被检测。

信封（envelope）是系统间交接的唯一合法载体：

    {
      "data_id": ...,
      "payload": ...,
      "label": {...},              # 必填，禁止裸传
      "lineage": [...],            # 继承链节点（含本节点）
      "handoffs": [                # 每次交接追加一条
        {"from_system": ..., "to_system": ..., "expected_label": {...},
         "received_label": {...}|null, "status": "...", "violations": [...]}
      ]
    }

``verify_envelope`` 在接收侧执行，检测：
  MISSING_LABEL        下游丢掉了标签
  LABEL_TAMPERED       标签与交接时声明的期望标签不一致
  LABEL_WEAKENED       标签被改宽（level 降低或限制减少）——泄露风险
  LINEAGE_BROKEN       继承链缺失/断裂/派生标签未覆盖来源
  UNLABELED_SOURCE     链上存在无标签数据，需人工确认来源
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .labels import Label, UnknownLabelError, label_from_raw
from .lineage import Lineage, LineageNode, TaggedData


class EnvelopeError(ValueError):
    """信封结构非法。"""


@dataclass(frozen=True)
class VerificationReport:
    """接收侧检测结果。"""

    ok: bool
    status: str
    violations: Tuple[str, ...] = ()
    label: Optional[Label] = None

    def __str__(self) -> str:
        head = f"[{self.status}]"
        if not self.violations:
            return head
        return head + " " + "; ".join(self.violations)


def pack_envelope(data: TaggedData) -> dict:
    """把 TaggedData 打包成可跨系统传输的信封。"""
    return {
        "data_id": data.data_id,
        "payload": copy.deepcopy(data.payload),
        "label": data.label.to_raw(),
        "lineage": [node.to_raw() for node in data.lineage.chain_to(data.data_id)],
        "handoffs": [],
    }


def record_handoff(
    envelope: dict,
    from_system: str,
    to_system: str,
    received_label_raw: Optional[dict],
) -> VerificationReport:
    """登记一次系统间交接并检测标签是否被下游丢掉/改宽。

    ``received_label_raw`` 是下游实际随数据携带的标签；传 ``None``
    表示下游把标签丢了。
    """
    expected_raw = envelope.get("label")
    if expected_raw is None:
        raise EnvelopeError("信封缺少 label 字段")
    expected = label_from_raw(expected_raw)

    violations: List[str] = []
    status = "OK"
    received: Optional[Label] = None

    if received_label_raw is None:
        status = "MISSING_LABEL"
        violations.append(
            f"下游系统 {to_system} 未携带标签，期望标签 {expected} 已丢失"
        )
    else:
        try:
            received = label_from_raw(received_label_raw)
        except UnknownLabelError as exc:
            status = "LABEL_TAMPERED"
            violations.append(f"下游标签无法识别: {exc}")
        else:
            if received != expected:
                if expected.covers(received):
                    status = "LABEL_WEAKENED"
                    violations.append(
                        f"标签被放宽: 期望 {expected}，实际 {received}"
                    )
                else:
                    status = "LABEL_TAMPERED"
                    violations.append(
                        f"标签被篡改: 期望 {expected}，实际 {received}"
                    )

    report = VerificationReport(
        ok=status == "OK",
        status=status,
        violations=tuple(violations),
        label=received if received is not None else expected,
    )
    envelope.setdefault("handoffs", []).append(
        {
            "from_system": from_system,
            "to_system": to_system,
            "expected_label": expected_raw,
            "received_label": received_label_raw,
            "status": status,
            "violations": list(violations),
        }
    )
    return report


def verify_envelope(envelope: dict) -> VerificationReport:
    """接收侧整体校验：结构、标签、继承链完整性。"""
    violations: List[str] = []

    for key in ("data_id", "payload", "label", "lineage"):
        if key not in envelope:
            raise EnvelopeError(f"信封缺少字段: {key}")

    if envelope["label"] is None:
        violations.append("MISSING_LABEL: 信封未携带标签")
        label = None
    else:
        try:
            label = label_from_raw(envelope["label"])
        except UnknownLabelError as exc:
            violations.append(f"LABEL_TAMPERED: {exc}")
            label = None

    lineage_violations, unlabeled = _check_lineage(envelope["lineage"])
    violations.extend(lineage_violations)
    if unlabeled:
        violations.append(
            "UNLABELED_SOURCE: 继承链中存在无标签数据，需人工确认来源"
        )

    for handoff in envelope.get("handoffs", []):
        if handoff.get("status") != "OK":
            for item in handoff.get("violations", []):
                violations.append(f"{handoff.get('status')}: {item}")

    if violations:
        status = violations[0].split(":", 1)[0]
        return VerificationReport(
            ok=False, status=status, violations=tuple(violations), label=label
        )
    return VerificationReport(ok=True, status="OK", label=label)


def _check_lineage(raw_nodes: object) -> Tuple[List[str], bool]:
    """重建继承链并审计，返回 (违规列表, 是否含无标签节点)。"""
    violations: List[str] = []
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return ["LINEAGE_BROKEN: 继承链为空或缺失"], False

    lineage = Lineage()
    unlabeled = False
    try:
        for raw in raw_nodes:
            node = LineageNode(
                data_id=raw["data_id"],
                label=label_from_raw(raw["label"]),
                created_at=raw.get("created_at", ""),
                system=raw.get("system", ""),
                parents=tuple(raw.get("parents", [])),
                payload_digest=raw.get("payload_digest", ""),
            )
            unlabeled = unlabeled or node.label.is_unlabeled
            lineage.add(node)
    except (KeyError, TypeError, UnknownLabelError, ValueError) as exc:
        violations.append(f"LINEAGE_BROKEN: 继承链无法重建 ({exc})")
        return violations, unlabeled

    for problem in lineage.audit():
        violations.append(f"LINEAGE_BROKEN: {problem}")
    return violations, unlabeled
