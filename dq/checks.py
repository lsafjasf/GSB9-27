"""五类质量检查：完整性、唯一性、取值范围、类型一致性、分布漂移。

所有检查均为流式单次扫描：update() 逐行喂数据，finalize() 产出
可解释的 CheckResult（字段、规则、命中样本、命中比例、阈值、解释）。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from .core import CheckResult
from . import drift as drift_mod

MAX_SAMPLES_DEFAULT = 5


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


class BaseCheck:
    rule_type = "base"

    def __init__(self, rule: Dict[str, Any]) -> None:
        self.rule = rule
        self.field = rule.get("field") or rule.get("fields")
        self.rule_id = rule.get("id") or f"{self.rule_type}:{self.field}"
        self.severity = rule.get("severity", "error")
        self.max_samples = int(rule.get("max_samples", MAX_SAMPLES_DEFAULT))
        self.samples: List[Dict[str, Any]] = []
        self.hit_count = 0
        self.threshold: Optional[float] = None

    def _add_sample(self, row: int, value: Any, reason: str) -> None:
        if len(self.samples) < self.max_samples:
            self.samples.append({"row": row, "value": value, "reason": reason})

    def update(self, row_idx: int, row: Dict[str, Any]) -> None:  # pragma: no cover
        raise NotImplementedError

    def finalize(self, total_rows: int) -> CheckResult:  # pragma: no cover
        raise NotImplementedError

    def _no_data(self, total_rows: int, note: str = "数据集为空，无行可检") -> CheckResult:
        return CheckResult(
            rule_id=self.rule_id,
            rule_type=self.rule_type,
            field=self.field,
            status="no_data",
            total_rows=total_rows,
            hit_count=0,
            hit_ratio=0.0,
            threshold=self.threshold,
            samples=[],
            explanation=note,
            severity=self.severity,
        )

    def _result(self, total_rows: int, ratio: float, explanation: str,
                details: Optional[Dict[str, Any]] = None) -> CheckResult:
        status = "fail" if (self.threshold is not None and ratio > self.threshold) else "pass"
        return CheckResult(
            rule_id=self.rule_id,
            rule_type=self.rule_type,
            field=self.field,
            status=status,
            total_rows=total_rows,
            hit_count=self.hit_count,
            hit_ratio=ratio,
            threshold=self.threshold,
            samples=self.samples,
            explanation=explanation,
            severity=self.severity,
            details=details or {},
        )


class CompletenessCheck(BaseCheck):
    """完整性：统计字段缺失（null / 空串 / 字段不存在）比例。"""

    rule_type = "completeness"

    def __init__(self, rule: Dict[str, Any]) -> None:
        super().__init__(rule)
        self.threshold = float(rule.get("max_missing_ratio", 0.0))

    def update(self, row_idx: int, row: Dict[str, Any]) -> None:
        value = row.get(self.field)
        if _is_missing(value):
            self.hit_count += 1
            shown = "<字段不存在>" if self.field not in row else value
            self._add_sample(row_idx, shown, "字段缺失")

    def finalize(self, total_rows: int) -> CheckResult:
        if total_rows == 0:
            return self._no_data(total_rows)
        ratio = self.hit_count / total_rows
        explanation = (
            f"字段 {self.field} 缺失 {self.hit_count}/{total_rows} 行"
            f"（{ratio:.2%}），阈值 {self.threshold:.2%}"
        )
        return self._result(total_rows, ratio, explanation)


class UniquenessCheck(BaseCheck):
    """唯一性：统计重复键（单字段或多字段组合）比例。"""

    rule_type = "uniqueness"

    def __init__(self, rule: Dict[str, Any]) -> None:
        super().__init__(rule)
        fields = rule.get("fields") or [rule["field"]]
        self.fields = list(fields)
        self.field = self.fields
        self.threshold = float(rule.get("max_duplicate_ratio", 0.0))
        self.seen: set = set()
        self.first_row: Dict[tuple, int] = {}

    def update(self, row_idx: int, row: Dict[str, Any]) -> None:
        key = tuple(row.get(f) for f in self.fields)
        if key in self.seen:
            self.hit_count += 1
            self._add_sample(
                row_idx,
                {f: row.get(f) for f in self.fields},
                f"与第 {self.first_row[key]} 行重复",
            )
        else:
            self.seen.add(key)
            self.first_row[key] = row_idx

    def finalize(self, total_rows: int) -> CheckResult:
        if total_rows == 0:
            return self._no_data(total_rows)
        ratio = self.hit_count / total_rows
        explanation = (
            f"键 {self.fields} 重复 {self.hit_count}/{total_rows} 行"
            f"（{ratio:.2%}），阈值 {self.threshold:.2%}，唯一键 {len(self.seen)} 个"
        )
        return self._result(total_rows, ratio, explanation,
                            details={"unique_keys": len(self.seen)})


class RangeCheck(BaseCheck):
    """取值范围：数值必须落在 [min, max] 内；非数值也算命中。"""

    rule_type = "range"

    def __init__(self, rule: Dict[str, Any]) -> None:
        super().__init__(rule)
        self.min = rule.get("min")
        self.max = rule.get("max")
        self.threshold = float(rule.get("max_violation_ratio", 0.0))
        self.evaluated = 0
        self.skipped_missing = 0

    def update(self, row_idx: int, row: Dict[str, Any]) -> None:
        value = row.get(self.field)
        if _is_missing(value):
            self.skipped_missing += 1
            return  # 缺失由完整性规则负责
        self.evaluated += 1
        num = drift_mod._to_float(value)
        if num is None:
            self.hit_count += 1
            self._add_sample(row_idx, value, "非数值，无法比较范围")
            return
        if (self.min is not None and num < self.min) or (self.max is not None and num > self.max):
            self.hit_count += 1
            self._add_sample(row_idx, value, f"超出范围 [{self.min}, {self.max}]")

    def finalize(self, total_rows: int) -> CheckResult:
        if total_rows == 0:
            return self._no_data(total_rows)
        if self.evaluated == 0:
            return self._no_data(total_rows, note=f"字段 {self.field} 全部缺失，范围检查无数据可评估")
        ratio = self.hit_count / self.evaluated
        explanation = (
            f"字段 {self.field} 超范围 {self.hit_count}/{self.evaluated} 行"
            f"（{ratio:.2%}），允许范围 [{self.min}, {self.max}]，阈值 {self.threshold:.2%}"
        )
        return self._result(total_rows, ratio, explanation,
                            details={"evaluated_rows": self.evaluated,
                                     "skipped_missing": self.skipped_missing})


_TYPE_CHECKS = {
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, float),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "str": lambda v: isinstance(v, str),
    "bool": lambda v: isinstance(v, bool),
    "list": lambda v: isinstance(v, list),
    "dict": lambda v: isinstance(v, dict),
}


class TypeConsistencyCheck(BaseCheck):
    """类型一致性：字段值的 Python 类型必须与声明类型一致。"""

    rule_type = "type_consistency"

    def __init__(self, rule: Dict[str, Any]) -> None:
        super().__init__(rule)
        expected = rule["expected"]
        if expected not in _TYPE_CHECKS:
            raise ValueError(f"未知类型 {expected}，支持：{sorted(_TYPE_CHECKS)}")
        self.expected = expected
        self.threshold = float(rule.get("max_mismatch_ratio", 0.0))
        self.evaluated = 0
        self.skipped_missing = 0

    def update(self, row_idx: int, row: Dict[str, Any]) -> None:
        value = row.get(self.field)
        if _is_missing(value):
            self.skipped_missing += 1
            return
        self.evaluated += 1
        if not _TYPE_CHECKS[self.expected](value):
            self.hit_count += 1
            self._add_sample(row_idx, value,
                             f"实际类型 {type(value).__name__}，期望 {self.expected}")

    def finalize(self, total_rows: int) -> CheckResult:
        if total_rows == 0:
            return self._no_data(total_rows)
        if self.evaluated == 0:
            return self._no_data(total_rows, note=f"字段 {self.field} 全部缺失，类型检查无数据可评估")
        ratio = self.hit_count / self.evaluated
        explanation = (
            f"字段 {self.field} 类型不符 {self.hit_count}/{self.evaluated} 行"
            f"（{ratio:.2%}），期望类型 {self.expected}，阈值 {self.threshold:.2%}"
        )
        return self._result(total_rows, ratio, explanation,
                            details={"evaluated_rows": self.evaluated,
                                     "skipped_missing": self.skipped_missing})


class DriftCheck(BaseCheck):
    """分布漂移：与冻结的基线快照比较（PSI），分箱边界不随新数据变化。"""

    rule_type = "drift"

    def __init__(self, rule: Dict[str, Any], base_dir: str = ".",
                 now: Optional[datetime] = None) -> None:
        super().__init__(rule)
        self.threshold = float(rule.get("psi_threshold", 0.25))
        self.bin_tolerance = float(rule.get("bin_tolerance", 0.05))
        baseline_path = rule["baseline"]
        if not os.path.isabs(baseline_path):
            baseline_path = os.path.join(base_dir, baseline_path)
        self.baseline = drift_mod.load_baseline(baseline_path)
        self.baseline_path = baseline_path
        self.now = now
        n_bins = len(self.baseline["bin_counts"])
        self.cur_counts = [0] * n_bins
        self.bin_samples: Dict[int, Dict[str, Any]] = {}
        self.evaluated = 0
        self.skipped_missing = 0

    def update(self, row_idx: int, row: Dict[str, Any]) -> None:
        value = row.get(self.field)
        idx = drift_mod.bin_index(self.baseline, value)
        if idx is None:
            self.skipped_missing += 1
            return
        self.evaluated += 1
        self.cur_counts[idx] += 1
        if idx not in self.bin_samples and len(self.bin_samples) < 64:
            self.bin_samples[idx] = {"row": row_idx, "value": value}

    def finalize(self, total_rows: int) -> CheckResult:
        if drift_mod.is_stale(self.baseline, self.now):
            age = drift_mod.baseline_age_days(self.baseline, self.now)
            return CheckResult(
                rule_id=self.rule_id,
                rule_type=self.rule_type,
                field=self.field,
                status="error",
                total_rows=total_rows,
                hit_count=0,
                hit_ratio=0.0,
                threshold=self.threshold,
                samples=[],
                explanation=(
                    f"基线 {self.baseline_path} 已过期：创建于 "
                    f"{self.baseline['created_at']}，已 {age:.1f} 天，"
                    f"超过有效期 {self.baseline['max_age_days']} 天，请重建基线后再判定"
                ),
                severity=self.severity,
                details={"baseline_age_days": round(age, 2),
                         "max_age_days": self.baseline["max_age_days"]},
            )
        if total_rows == 0:
            return self._no_data(total_rows)
        if self.evaluated == 0:
            return self._no_data(total_rows, note=f"字段 {self.field} 全部缺失，漂移检查无数据可评估")

        psi_value, per_bin = drift_mod.psi(self.baseline["bin_counts"], self.cur_counts)
        for entry in per_bin:
            entry["label"] = drift_mod.bin_label(self.baseline, entry["index"])
        per_bin.sort(key=lambda e: -e["contribution"])

        # 命中定义：占比偏移超过 bin_tolerance 的分箱中的当前数据行
        drifted = [e for e in per_bin
                   if abs(e["cur_ratio"] - e["base_ratio"]) > self.bin_tolerance]
        self.hit_count = sum(self.cur_counts[e["index"]] for e in drifted)
        ratio = self.hit_count / self.evaluated

        samples = []
        for entry in drifted[: self.max_samples]:
            sample = self.bin_samples.get(entry["index"])
            if sample:
                samples.append({
                    "row": sample["row"],
                    "value": sample["value"],
                    "reason": (
                        f"落入漂移分箱 {entry['label']}：基线占比 "
                        f"{entry['base_ratio']:.2%} → 当前 {entry['cur_ratio']:.2%}"
                    ),
                })
        self.samples = samples

        status = "fail" if psi_value > self.threshold else "pass"
        top = per_bin[0]
        explanation = (
            f"字段 {self.field} 分布漂移 PSI={psi_value:.4f}（阈值 {self.threshold}）；"
            f"漂移最大分箱 {top['label']}：基线 {top['base_ratio']:.2%} → "
            f"当前 {top['cur_ratio']:.2%}；命中（偏移>{self.bin_tolerance:.0%} 的分箱）"
            f"{self.hit_count}/{self.evaluated} 行（{ratio:.2%}）"
        )
        return CheckResult(
            rule_id=self.rule_id,
            rule_type=self.rule_type,
            field=self.field,
            status=status,
            total_rows=total_rows,
            hit_count=self.hit_count,
            hit_ratio=ratio,
            threshold=self.threshold,
            samples=self.samples,
            explanation=explanation,
            severity=self.severity,
            details={
                "psi": round(psi_value, 6),
                "baseline": self.baseline_path,
                "baseline_created_at": self.baseline["created_at"],
                "evaluated_rows": self.evaluated,
                "skipped_missing": self.skipped_missing,
                "top_bins": [
                    {
                        "bin": e["label"],
                        "base_ratio": round(e["base_ratio"], 6),
                        "cur_ratio": round(e["cur_ratio"], 6),
                        "psi_contribution": round(e["contribution"], 6),
                    }
                    for e in per_bin[:5]
                ],
            },
        )


def build_check(rule: Dict[str, Any], base_dir: str = ".",
                now: Optional[datetime] = None) -> BaseCheck:
    """按声明式规则构造检查实例。"""
    rule_type = rule.get("type")
    if rule_type == "completeness":
        return CompletenessCheck(rule)
    if rule_type == "uniqueness":
        return UniquenessCheck(rule)
    if rule_type == "range":
        return RangeCheck(rule)
    if rule_type == "type_consistency":
        return TypeConsistencyCheck(rule)
    if rule_type == "drift":
        return DriftCheck(rule, base_dir=base_dir, now=now)
    raise ValueError(f"未知规则类型: {rule_type}")
