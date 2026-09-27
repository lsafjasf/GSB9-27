"""核心数据结构：检查结果与报告。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 状态严重度排序（数值越大越严重），用于汇总整体状态
STATUS_ORDER = {"pass": 0, "no_data": 1, "fail": 2, "error": 3}


@dataclass
class CheckResult:
    """单条规则的检查结果，包含可解释信息。"""

    rule_id: str
    rule_type: str
    field: Any
    status: str  # pass | fail | error | no_data
    total_rows: int
    hit_count: int
    hit_ratio: float
    threshold: Optional[float]
    samples: List[Dict[str, Any]]  # [{"row": 行号, "value": 值, "reason": 原因}]
    explanation: str
    severity: str = "error"  # error | warn
    elapsed_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "field": self.field,
            "status": self.status,
            "severity": self.severity,
            "total_rows": self.total_rows,
            "hit_count": self.hit_count,
            "hit_ratio": round(self.hit_ratio, 6),
            "threshold": self.threshold,
            "samples": self.samples,
            "explanation": self.explanation,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "details": self.details,
        }


@dataclass
class Report:
    """一次检查运行的整体报告。"""

    results: List[CheckResult]
    total_rows: int

    @property
    def status(self) -> str:
        worst = "pass"
        for result in self.results:
            if STATUS_ORDER[result.status] > STATUS_ORDER[worst]:
                worst = result.status
        return worst

    @property
    def failed(self) -> List[CheckResult]:
        return [r for r in self.results if r.status in ("fail", "error")]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "total_rows": self.total_rows,
            "rule_count": len(self.results),
            "failed_count": len(self.failed),
            "checks": [r.to_dict() for r in self.results],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)
