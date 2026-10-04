"""实际调用测试：把同一批调用用例分别打到新旧两个实现上，对照结果，
并与静态判定结果做一致性核对，列出不一致条目。
"""
from __future__ import annotations

import importlib.util
import json
import traceback
from dataclasses import dataclass, field
from typing import Any

from .compare import (
    BREAKING, CAUTION, COMPATIBLE, CompareReport, Judgment,
    PARAM_DEFAULT_CHANGED, RETURN_EXTENDED,
)

# 运行时结果分类
SAME = "same"                    # 两版都成功且结果相等
DIFFERENT = "different"          # 两版都成功但结果不同
V2_ERROR = "v2_error"            # 旧版成功、新版报错
V1_ERROR = "v1_error"            # 旧版报错、新版成功
BOTH_ERROR_SAME = "both_error_same"
BOTH_ERROR_DIFF = "both_error_diff"

OUTCOME_LABEL = {
    SAME: "结果一致",
    DIFFERENT: "结果不同",
    V2_ERROR: "新版报错",
    V1_ERROR: "旧版报错",
    BOTH_ERROR_SAME: "两版同错",
    BOTH_ERROR_DIFF: "两版不同错",
}

# 不一致类型
MISS = "漏报"        # 静态判兼容/低风险，实际调用失败
FALSE_ALARM = "误报"  # 静态判破坏性，实际调用正常
SUSPECT = "可疑"      # 静态判兼容，实际行为已变化


@dataclass
class CallCase:
    id: str
    function: str
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass
class CaseResult:
    case: CallCase
    outcome: str
    old_value: Any = None
    new_value: Any = None
    old_error: str = ""
    new_error: str = ""
    added_keys: list[str] = field(default_factory=list)   # 新版结果多出的键
    removed_keys: list[str] = field(default_factory=list)  # 新版结果少掉的键


@dataclass
class Inconsistency:
    case_id: str
    function: str
    kind: str  # 漏报 / 误报 / 可疑
    detail: str


def load_cases(path: str) -> list[CallCase]:
    with open(path, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    return [
        CallCase(
            id=c["id"],
            function=c["function"],
            args=c.get("args", []),
            kwargs=c.get("kwargs", {}),
            note=c.get("note", ""),
        )
        for c in data["cases"]
    ]


def load_impl(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoke(module, case: CallCase):
    fn = getattr(module, case.function)
    return fn(*case.args, **case.kwargs)


def _diff_keys(old_value: Any, new_value: Any):
    added: list[str] = []
    removed: list[str] = []
    if isinstance(old_value, dict) and isinstance(new_value, dict):
        added = sorted(set(new_value) - set(old_value))
        removed = sorted(set(old_value) - set(new_value))
    return added, removed


def run_cases(cases: list[CallCase], old_impl, new_impl) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in cases:
        old_ok, old_value, old_error = True, None, ""
        new_ok, new_value, new_error = True, None, ""
        try:
            old_value = _invoke(old_impl, case)
        except Exception as exc:  # noqa: BLE001 - 需要记录任意实现异常
            old_ok, old_error = False, f"{type(exc).__name__}: {exc}"
        try:
            new_value = _invoke(new_impl, case)
        except Exception as exc:  # noqa: BLE001
            new_ok, new_error = False, f"{type(exc).__name__}: {exc}"

        if old_ok and new_ok:
            outcome = SAME if old_value == new_value else DIFFERENT
        elif old_ok and not new_ok:
            outcome = V2_ERROR
        elif not old_ok and new_ok:
            outcome = V1_ERROR
        else:
            outcome = (BOTH_ERROR_SAME if type(old_error) is type(new_error)
                       and old_error.split(":")[0] == new_error.split(":")[0]
                       else BOTH_ERROR_DIFF)
        added, removed = _diff_keys(old_value, new_value)
        results.append(CaseResult(
            case=case, outcome=outcome,
            old_value=old_value, new_value=new_value,
            old_error=old_error, new_error=new_error,
            added_keys=added, removed_keys=removed,
        ))
    return results


def check_consistency(report: CompareReport,
                      results: list[CaseResult]) -> list[Inconsistency]:
    """把运行时结果与静态判定对照，返回不一致条目清单。"""
    out: list[Inconsistency] = []
    for res in results:
        case = res.case
        kinds = report.kinds_for_function(case.function)
        exercised = {k for k in kinds if k not in ("FUNCTION_ADDED",)}
        if not exercised:
            static = COMPATIBLE
        elif any(report_level == BREAKING for report_level in
                 (j.level for j in report.for_function(case.function))):
            static = BREAKING
        else:
            static = CAUTION

        if res.outcome == V2_ERROR:
            if static in (COMPATIBLE, CAUTION):
                out.append(Inconsistency(
                    case.id, case.function, MISS,
                    f"静态判定为{static}，但实际调用在新版本报错：{res.new_error}",
                ))
        elif res.outcome == SAME:
            if static == BREAKING:
                out.append(Inconsistency(
                    case.id, case.function, FALSE_ALARM,
                    "静态判定为破坏性，但该用例在新版本调用成功且结果一致"
                    "（可能仅影响其他调用方式，如位置传参）。",
                ))
        elif res.outcome == DIFFERENT:
            if static == COMPATIBLE:
                out.append(Inconsistency(
                    case.id, case.function, SUSPECT,
                    "静态判定为兼容，但实际行为已变化"
                    f"（新增键={res.added_keys} 缺失键={res.removed_keys}）。",
                ))
            elif static == CAUTION:
                soft = {PARAM_DEFAULT_CHANGED, RETURN_EXTENDED}
                if exercised and exercised <= soft:
                    pass  # 预期内的行为差异：默认值变化 / 返回扩展
                else:
                    out.append(Inconsistency(
                        case.id, case.function, SUSPECT,
                        "结果差异超出已登记的低风险条目范围，需人工复核。",
                    ))
    return out
