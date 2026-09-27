"""进程内运行时：基于 contextvars，天然支持 asyncio 并发子任务。

跨线程 / 跨进程 / 消息队列边界必须显式 inject -> 传递 carrier -> extract。
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

from .context import Span, SpanContext, new_span_id, new_trace_id

_current: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "tracectx_current_span", default=None
)


def current_span() -> Span | None:
    return _current.get()


def start_root_span(name: str, sampled: bool = True) -> Span:
    """无上下文入口：开启一条新链路，采样决策在此做出并随后传播。"""
    return Span(
        name=name,
        context=SpanContext(
            trace_id=new_trace_id(),
            span_id=new_span_id(),
            parent_span_id=None,
            sampled=sampled,
        ),
    )


def start_span(name: str, parent: Span | SpanContext | None = None) -> Span:
    """开启子 Span。trace_id 与 sampled 继承自父上下文，下游无法更改。"""
    if parent is None:
        parent = _current.get()
    if parent is None:
        return start_root_span(name)
    pctx = parent.context if isinstance(parent, Span) else parent
    return Span(
        name=name,
        context=SpanContext(
            trace_id=pctx.trace_id,
            span_id=new_span_id(),
            parent_span_id=pctx.span_id,
            sampled=pctx.sampled,  # 采样决策只随上下文传播，此处没有重采样入口
        ),
    )


@contextmanager
def use_span(span: Span) -> Iterator[Span]:
    """把 span 设为当前上下文（asyncio 任务间通过 contextvars 自动隔离）。"""
    token = _current.set(span)
    try:
        yield span
    finally:
        _current.reset(token)
