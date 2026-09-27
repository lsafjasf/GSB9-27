"""不可变的 Span / SpanContext 数据结构。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


def new_trace_id() -> str:
    return uuid.uuid4().hex  # 32 hex chars


def new_span_id() -> str:
    return uuid.uuid4().hex[:16]  # 16 hex chars


@dataclass(frozen=True)
class SpanContext:
    """可跨进程传播的追踪上下文（不可变，采样位一旦设定不可更改）。"""

    trace_id: str
    span_id: str
    sampled: bool
    parent_span_id: str | None = None


@dataclass(frozen=True)
class Span:
    """一个链路节点。context 携带传播所需全部信息。"""

    name: str
    context: SpanContext
    start_ns: int = field(default_factory=time.time_ns)
    end_ns: int | None = None

    @property
    def trace_id(self) -> str:
        return self.context.trace_id

    @property
    def span_id(self) -> str:
        return self.context.span_id

    @property
    def parent_span_id(self) -> str | None:
        return self.context.parent_span_id

    @property
    def sampled(self) -> bool:
        return self.context.sampled

    def finish(self, end_ns: int | None = None) -> "Span":
        """返回结束后的新 Span（保持不可变语义）。"""
        return Span(
            name=self.name,
            context=self.context,
            start_ns=self.start_ns,
            end_ns=end_ns if end_ns is not None else time.time_ns(),
        )
