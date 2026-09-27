"""文本报告渲染（JSON 报告由 Report.to_json 提供）。"""
from __future__ import annotations

from .core import Report

_STATUS_ICON = {"pass": "PASS", "fail": "FAIL", "error": "ERROR", "no_data": "NO_DATA"}


def render_text(report: Report) -> str:
    lines = [
        f"数据质量报告：{report.total_rows} 行，{len(report.results)} 条规则，"
        f"整体状态 {report.status.upper()}",
        "-" * 72,
    ]
    for result in report.results:
        icon = _STATUS_ICON.get(result.status, result.status)
        lines.append(
            f"[{icon:7}] {result.rule_id} ({result.rule_type}, 字段={result.field}) "
            f"命中 {result.hit_count}/{result.total_rows} "
            f"({result.hit_ratio:.2%}) 耗时 {result.elapsed_ms:.1f}ms"
        )
        lines.append(f"          {result.explanation}")
        for sample in result.samples:
            lines.append(
                f"          样本 行{sample['row']}: {sample['value']!r} — {sample['reason']}"
            )
    return "\n".join(lines)
