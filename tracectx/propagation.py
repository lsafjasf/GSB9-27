"""注入 / 提取：确定性序列化格式 + 可选 HMAC 防篡改。

线格式（deterministic，字段顺序固定，可直接放进 HTTP 头 / MQ 消息体 / 环境变量）：

    tc1.<trace_id>.<span_id>.<parent_span_id|->.<flags>[.<sig>]

- trace_id       : 32 位小写 hex
- span_id        : 16 位小写 hex
- parent_span_id : 16 位小写 hex，根节点用 "-"
- flags          : "01" 采样 / "00" 不采样
- sig            : 配置密钥时追加 HMAC-SHA256(key, 前四段) 的前 32 hex
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Mapping, MutableMapping

from .context import Span, SpanContext

FORMAT_VERSION = "tc1"
CARRIER_KEY = "x-trace-context"

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_SIG = re.compile(r"^[0-9a-f]{32}$")


class InvalidContextError(ValueError):
    """格式非法的上下文。"""


class TamperedContextError(ValueError):
    """签名校验失败：上下文被篡改或密钥不匹配。"""


def _sign(payload: str, key: bytes) -> str:
    return hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()[:32]


def serialize(ctx: SpanContext, key: bytes | None = None) -> str:
    """把上下文序列化为确定性字符串。"""
    parent = ctx.parent_span_id if ctx.parent_span_id else "-"
    flags = "01" if ctx.sampled else "00"
    payload = f"{FORMAT_VERSION}.{ctx.trace_id}.{ctx.span_id}.{parent}.{flags}"
    if key is not None:
        return f"{payload}.{_sign(payload, key)}"
    return payload


def deserialize(raw: str, key: bytes | None = None) -> SpanContext:
    """解析字符串；格式非法抛 InvalidContextError，签名不符抛 TamperedContextError。"""
    if not isinstance(raw, str):
        raise InvalidContextError(f"context must be str, got {type(raw).__name__}")
    parts = raw.split(".")
    if len(parts) not in (5, 6):
        raise InvalidContextError(f"expected 5 or 6 fields, got {len(parts)}")
    version, trace_id, span_id, parent, flags = parts[:5]
    if version != FORMAT_VERSION:
        raise InvalidContextError(f"unsupported version: {version!r}")
    if not _HEX32.match(trace_id):
        raise InvalidContextError(f"bad trace_id: {trace_id!r}")
    if not _HEX16.match(span_id):
        raise InvalidContextError(f"bad span_id: {span_id!r}")
    if parent != "-" and not _HEX16.match(parent):
        raise InvalidContextError(f"bad parent_span_id: {parent!r}")
    if flags not in ("00", "01"):
        raise InvalidContextError(f"bad flags: {flags!r}")
    if key is not None:
        if len(parts) != 6 or not _SIG.match(parts[5]):
            raise TamperedContextError("missing or malformed signature")
        payload = ".".join(parts[:5])
        if not hmac.compare_digest(_sign(payload, key), parts[5]):
            raise TamperedContextError("signature mismatch: context tampered")
    elif len(parts) == 6:
        raise InvalidContextError("unexpected signature field (no key configured)")
    return SpanContext(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=None if parent == "-" else parent,
        sampled=flags == "01",
    )


def inject(
    span: Span | SpanContext,
    carrier: MutableMapping[str, str],
    key: bytes | None = None,
) -> MutableMapping[str, str]:
    """把上下文注入载体（HTTP 头 dict / MQ 消息属性 / 子进程 env）。"""
    ctx = span.context if isinstance(span, Span) else span
    carrier[CARRIER_KEY] = serialize(ctx, key=key)
    return carrier


def extract(
    carrier: Mapping[str, str],
    key: bytes | None = None,
) -> SpanContext | None:
    """从载体提取上下文。无上下文返回 None；被篡改抛 TamperedContextError。"""
    raw = carrier.get(CARRIER_KEY)
    if raw is None:
        return None
    return deserialize(raw, key=key)
