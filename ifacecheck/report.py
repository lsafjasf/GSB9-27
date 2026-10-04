"""把判定结果、运行时对照结果渲染成 Markdown 表格。"""
from __future__ import annotations

from .compare import CompareReport, LEVEL_LABEL
from .runtime import CaseResult, Inconsistency, OUTCOME_LABEL


def _esc(text: str) -> str:
    return text.replace("|", "\\|")


def render_judgment_table(report: CompareReport) -> str:
    lines = [
        f"## 判定表（{report.old_version} -> {report.new_version}）",
        "",
        f"总体结论：**{LEVEL_LABEL[report.max_level]}**",
        "",
        "| 函数 | 判定项 | 影响等级 | 对象 | 说明 | 依据 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if not report.judgments:
        lines.append("| - | 无变化 | 兼容 | - | 两版接口定义完全一致 | - |")
    for j in report.judgments:
        lines.append(
            f"| {j.function} | {j.kind} | {LEVEL_LABEL[j.level]} "
            f"| {j.target} | {_esc(j.detail)} | {_esc(j.rationale)} |"
        )
    return "\n".join(lines)


def render_runtime_table(results: list[CaseResult]) -> str:
    lines = [
        "## 实际调用对照表",
        "",
        "| 用例 | 函数 | 结果 | 旧版本 | 新版本 | 备注 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        old_cell = repr(r.old_value) if r.outcome not in ("v1_error",) else r.old_error
        new_cell = repr(r.new_value) if r.outcome != "v2_error" else r.new_error
        if r.outcome in ("v1_error",):
            old_cell = r.old_error
        if r.outcome == "v2_error":
            new_cell = r.new_error
        if r.outcome in ("both_error_same", "both_error_diff"):
            old_cell, new_cell = r.old_error, r.new_error
        lines.append(
            f"| {r.case.id} | {r.case.function} | {OUTCOME_LABEL[r.outcome]} "
            f"| {_esc(str(old_cell))} | {_esc(str(new_cell))} "
            f"| {_esc(r.case.note)} |"
        )
    return "\n".join(lines)


def render_inconsistency_table(items: list[Inconsistency]) -> str:
    lines = [
        "## 静态判定 vs 实际调用：不一致条目清单",
        "",
    ]
    if not items:
        lines.append("无不一致条目：静态判定与实际调用测试相互印证。")
        return "\n".join(lines)
    lines += [
        "| 用例 | 函数 | 不一致类型 | 说明 |",
        "| --- | --- | --- | --- |",
    ]
    for it in items:
        lines.append(f"| {it.case_id} | {it.function} | {it.kind} | {_esc(it.detail)} |")
    return "\n".join(lines)


def render_full(report: CompareReport, results: list[CaseResult] | None,
                inconsistencies: list[Inconsistency] | None) -> str:
    parts = [render_judgment_table(report)]
    if results is not None:
        parts.append(render_runtime_table(results))
    if inconsistencies is not None:
        parts.append(render_inconsistency_table(inconsistencies))
    return "\n\n".join(parts)
