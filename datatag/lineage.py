"""标签随数据流转：TaggedData + 继承链（Lineage）。

- ``TaggedData.source`` 登记原始数据（必须带标签，无标签用 UNLABELED 并告警）；
- ``TaggedData.derive`` 派生数据，标签 = 所有来源标签的收紧合并；
- ``Lineage`` 记录完整继承链，可序列化、可审计（派生标签是否覆盖全部来源）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .labels import Label, MergeResult, label_from_raw, merge_labels


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class DerivationOp:
    """派生操作的登记信息（谁在哪个系统做了什么变换）。"""

    system: str
    transform: str
    note: str = ""

    def to_raw(self) -> dict:
        return {"system": self.system, "transform": self.transform, "note": self.note}


@dataclass(frozen=True)
class LineageNode:
    """继承链中的一个节点。"""

    data_id: str
    label: Label
    created_at: str
    system: str
    parents: Tuple[str, ...] = ()
    op: Optional[DerivationOp] = None
    payload_digest: str = ""

    def to_raw(self) -> dict:
        return {
            "data_id": self.data_id,
            "label": self.label.to_raw(),
            "created_at": self.created_at,
            "system": self.system,
            "parents": list(self.parents),
            "op": self.op.to_raw() if self.op else None,
            "payload_digest": self.payload_digest,
        }


@dataclass
class Lineage:
    """完整继承链：节点表 + 顺序，支持 JSON 序列化。"""

    nodes: Dict[str, LineageNode] = field(default_factory=dict)

    def add(self, node: LineageNode) -> None:
        if node.data_id in self.nodes:
            raise ValueError(f"重复的 data_id: {node.data_id}")
        for parent in node.parents:
            if parent not in self.nodes:
                raise ValueError(f"父节点未登记: {parent}")
        self.nodes[node.data_id] = node

    def chain_to(self, data_id: str) -> List[LineageNode]:
        """从根到该节点的完整继承链（拓扑序）。"""
        if data_id not in self.nodes:
            raise KeyError(data_id)
        ordered: List[LineageNode] = []
        seen = set()

        def visit(node_id: str) -> None:
            if node_id in seen:
                return
            seen.add(node_id)
            node = self.nodes[node_id]
            for parent in node.parents:
                visit(parent)
            ordered.append(node)

        visit(data_id)
        return ordered

    def audit(self) -> List[str]:
        """审计继承链：每个派生节点的标签必须覆盖其全部来源。"""
        problems: List[str] = []
        for node in self.nodes.values():
            for parent_id in node.parents:
                parent = self.nodes[parent_id]
                if not node.label.covers(parent.label):
                    problems.append(
                        f"{node.data_id} 的标签 {node.label} 比来源 "
                        f"{parent_id} 的标签 {parent.label} 宽松"
                    )
        return problems

    def to_raw(self) -> dict:
        return {
            "nodes": [node.to_raw() for node in self.nodes.values()],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_raw(), ensure_ascii=False, indent=2)


def _digest(payload: object) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@dataclass
class TaggedData:
    """携带标签与继承链的数据单元。"""

    data_id: str
    payload: object
    label: Label
    lineage: Lineage
    system: str = "local"

    @classmethod
    def source(
        cls,
        data_id: str,
        payload: object,
        label: Label,
        system: str,
        lineage: Optional[Lineage] = None,
    ) -> "TaggedData":
        lineage = lineage if lineage is not None else Lineage()
        node = LineageNode(
            data_id=data_id,
            label=label,
            created_at=_utcnow(),
            system=system,
            payload_digest=_digest(payload),
        )
        lineage.add(node)
        return cls(
            data_id=data_id,
            payload=payload,
            label=label,
            lineage=lineage,
            system=system,
        )

    @classmethod
    def derive(
        cls,
        data_id: str,
        parents: List["TaggedData"],
        transform: DerivationOp,
        payload: object,
    ) -> "TaggedData":
        """派生：标签 = 全部来源标签的收紧合并，继承链共享并追加节点。"""
        if not parents:
            raise ValueError("派生数据必须至少有一个来源")
        lineage = parents[0].lineage
        for other in parents[1:]:
            if other.lineage is not lineage:
                for node in other.lineage.nodes.values():
                    if node.data_id not in lineage.nodes:
                        lineage.add(node)
        merged: MergeResult = merge_labels([p.label for p in parents])
        node = LineageNode(
            data_id=data_id,
            label=merged.label,
            created_at=_utcnow(),
            system=transform.system,
            parents=tuple(p.data_id for p in parents),
            op=transform,
            payload_digest=_digest(payload),
        )
        lineage.add(node)
        return cls(
            data_id=data_id,
            payload=payload,
            label=merged.label,
            lineage=lineage,
            system=transform.system,
        )
