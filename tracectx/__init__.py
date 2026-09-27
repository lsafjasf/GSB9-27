"""tracectx: 跨服务/异步任务的追踪上下文传播库（仅标准库）。

核心能力：
- inject/extract：确定性序列化格式，可跨进程传递（HTTP 头、MQ 消息、环境变量）。
- 采样决策随上下文传播，下游不可更改（Span 为不可变对象，无重采样接口）。
- TraceValidator：校验链路完整性（缺失父节点、span_id 重用）。
- HMAC 签名：上下文被篡改时 extract 拒绝并报告。
"""

from .context import SpanContext, Span
from .propagation import (
    inject,
    extract,
    serialize,
    deserialize,
    TamperedContextError,
    InvalidContextError,
    FORMAT_VERSION,
)
from .runtime import (
    current_span,
    start_span,
    start_root_span,
    use_span,
)
from .validator import TraceValidator, IntegrityViolation

__all__ = [
    "SpanContext",
    "Span",
    "inject",
    "extract",
    "serialize",
    "deserialize",
    "TamperedContextError",
    "InvalidContextError",
    "FORMAT_VERSION",
    "current_span",
    "start_span",
    "start_root_span",
    "use_span",
    "TraceValidator",
    "IntegrityViolation",
]
