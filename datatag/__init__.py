"""数据合规标签：合并、传播与丢失检测（仅依赖 Python 标准库）。"""

from .labels import (
    ALL_RESTRICTIONS,
    Level,
    CONFIDENTIAL,
    INTERNAL,
    PUBLIC,
    RESTRICTED,
    SECRET,
    UNLABELED,
    Label,
    MergeResult,
    UnknownLabelError,
    label_from_raw,
    merge_labels,
    merge_rule_table,
)
from .lineage import DerivationOp, Lineage, LineageNode, TaggedData
from .transport import (
    EnvelopeError,
    VerificationReport,
    pack_envelope,
    record_handoff,
    verify_envelope,
)

__all__ = [
    "Label",
    "Level",
    "PUBLIC",
    "INTERNAL",
    "CONFIDENTIAL",
    "RESTRICTED",
    "SECRET",
    "UNLABELED",
    "ALL_RESTRICTIONS",
    "UnknownLabelError",
    "MergeResult",
    "merge_labels",
    "merge_rule_table",
    "label_from_raw",
    "Lineage",
    "LineageNode",
    "DerivationOp",
    "TaggedData",
    "pack_envelope",
    "record_handoff",
    "verify_envelope",
    "VerificationReport",
    "EnvelopeError",
]
