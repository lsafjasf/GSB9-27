"""链路完整性校验：收集各服务上报的 Span，检测结构违规。

可检出的问题：
- missing_parent : Span 声明了 parent_span_id，但同一 trace 中不存在该父节点
                   （消息队列 / 后台任务处断链的典型症状）
- duplicate_span_id : 同一 trace 中同一 span_id 被使用两次
- duplicate_trace_root : 同一 trace 出现多个根节点
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass

from .context import Span


@dataclass(frozen=True)
class IntegrityViolation:
    kind: str  # "missing_parent" | "duplicate_span_id" | "duplicate_trace_root"
    trace_id: str
    span_id: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] trace={self.trace_id} span={self.span_id}: {self.detail}"


class TraceValidator:
    """线程安全的 Span 收集器 + 完整性校验。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spans: dict[str, list[Span]] = defaultdict(list)  # trace_id -> spans

    def record(self, span: Span) -> None:
        with self._lock:
            self._spans[span.trace_id].append(span)

    def validate(self, trace_id: str) -> list[IntegrityViolation]:
        with self._lock:
            spans = list(self._spans.get(trace_id, []))
        violations: list[IntegrityViolation] = []

        seen: dict[str, int] = defaultdict(int)
        for s in spans:
            seen[s.span_id] += 1
        for span_id, count in seen.items():
            if count > 1:
                violations.append(
                    IntegrityViolation(
                        kind="duplicate_span_id",
                        trace_id=trace_id,
                        span_id=span_id,
                        detail=f"span_id reused {count} times",
                    )
                )

        ids = set(seen)
        roots = 0
        for s in spans:
            if s.parent_span_id is None:
                roots += 1
            elif s.parent_span_id not in ids:
                violations.append(
                    IntegrityViolation(
                        kind="missing_parent",
                        trace_id=trace_id,
                        span_id=s.span_id,
                        detail=(
                            f"parent {s.parent_span_id} not found in trace "
                            f"(span name={s.name!r})"
                        ),
                    )
                )
        if roots > 1:
            violations.append(
                IntegrityViolation(
                    kind="duplicate_trace_root",
                    trace_id=trace_id,
                    span_id="-",
                    detail=f"{roots} root spans in one trace",
                )
            )
        return violations

    def report(self, trace_id: str) -> str:
        violations = self.validate(trace_id)
        if not violations:
            return f"trace {trace_id}: OK ({len(self._spans.get(trace_id, []))} spans)"
        lines = [f"trace {trace_id}: {len(violations)} violation(s)"]
        lines.extend(f"  - {v}" for v in violations)
        return "\n".join(lines)
