"""Trace context propagation across services, queues and background tasks.

Standard library only.

Design:
  - TraceContext is an immutable value. The sampling decision is made once
    at the trace root and is inherited by every child; there is no API to
    change it downstream.
  - Serialization is deterministic and signed (HMAC-SHA256, truncated).
    Any tampering with trace_id / span_id / parent_id / sampled is detected
    at extraction time.
  - inject()/extract() move the context through any string carrier
    (HTTP headers, message metadata, env vars) so queue and process
    boundaries are crossed explicitly.
  - IntegrityValidator collects SpanRecords (thread-safe) and reports
    missing parents, duplicate span ids and sampling inconsistencies.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass, field

FORMAT_VERSION = "v1"
HEADER_NAME = "traceparent"
NO_PARENT = "-"
_TRACE_ID_LEN = 32  # 16 bytes hex
_SPAN_ID_LEN = 16   # 8 bytes hex
_SIG_LEN = 32       # 16 bytes of HMAC-SHA256, hex


class TraceError(Exception):
    """Base class for trace context errors."""


class MalformedContextError(TraceError):
    """The serialized context does not match the expected format."""


class TamperedContextError(TraceError):
    """The serialized context failed signature verification."""


def _is_hex(value: str, length: int) -> bool:
    if len(value) != length:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class TraceContext:
    """Immutable trace context. `sampled` is fixed at the root and only
    ever inherited, never overridden downstream."""

    trace_id: str
    span_id: str
    parent_id: str | None
    sampled: bool
    _secret: bytes = field(repr=False, compare=False)

    @classmethod
    def root(cls, secret: bytes, sampled: bool | None = None) -> "TraceContext":
        """Entry point when no upstream context exists. The sampling
        decision is made exactly once, here."""
        if sampled is None:
            sampled = secrets.randbelow(2) == 0
        return cls(secrets.token_hex(16), secrets.token_hex(8), None, sampled, secret)

    def child(self) -> "TraceContext":
        """Derive a child context. trace_id and the sampling decision are
        inherited unchanged; only the span identity advances."""
        return TraceContext(
            self.trace_id, secrets.token_hex(8), self.span_id, self.sampled, self._secret
        )

    def serialize(self) -> str:
        """Deterministic wire format:
        v1.<trace_id>.<span_id>.<parent_id|->.<0|1>.<hmac>"""
        parent = self.parent_id if self.parent_id else NO_PARENT
        body = f"{FORMAT_VERSION}.{self.trace_id}.{self.span_id}.{parent}.{int(self.sampled)}"
        sig = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).hexdigest()[:_SIG_LEN]
        return f"{body}.{sig}"

    @classmethod
    def deserialize(cls, value: str, secret: bytes) -> "TraceContext":
        parts = value.split(".")
        if len(parts) != 6 or parts[0] != FORMAT_VERSION:
            raise MalformedContextError(f"unrecognized context format: {value!r}")
        _, trace_id, span_id, parent_id, flag, sig = parts
        if not _is_hex(trace_id, _TRACE_ID_LEN):
            raise MalformedContextError("bad trace_id")
        if not _is_hex(span_id, _SPAN_ID_LEN):
            raise MalformedContextError("bad span_id")
        if parent_id != NO_PARENT and not _is_hex(parent_id, _SPAN_ID_LEN):
            raise MalformedContextError("bad parent_id")
        if flag not in ("0", "1"):
            raise MalformedContextError("bad sampled flag")
        body = ".".join(parts[:5])
        expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()[:_SIG_LEN]
        if not hmac.compare_digest(sig, expected):
            raise TamperedContextError(
                "signature mismatch: context was tampered with or wrong secret"
            )
        return cls(
            trace_id,
            span_id,
            None if parent_id == NO_PARENT else parent_id,
            flag == "1",
            secret,
        )

    def inject(self, carrier: dict) -> dict:
        """Write the context into a string carrier (headers / message meta)."""
        carrier[HEADER_NAME] = self.serialize()
        return carrier

    @classmethod
    def extract(cls, carrier: dict, secret: bytes) -> "TraceContext | None":
        """Read the context from a carrier. Returns None when absent,
        raises TamperedContextError / MalformedContextError when invalid."""
        raw = carrier.get(HEADER_NAME)
        if raw is None:
            return None
        return cls.deserialize(raw, secret)


@dataclass(frozen=True)
class SpanRecord:
    """One observed span, reported by a service for integrity validation."""

    trace_id: str
    span_id: str
    parent_id: str | None
    sampled: bool
    service: str
    operation: str

    @classmethod
    def from_context(cls, ctx: TraceContext, service: str, operation: str) -> "SpanRecord":
        return cls(ctx.trace_id, ctx.span_id, ctx.parent_id, ctx.sampled, service, operation)


@dataclass(frozen=True)
class IntegrityReport:
    missing_parents: tuple  # SpanRecords whose parent_id was never observed
    duplicate_span_ids: tuple  # span_ids recorded more than once
    sampling_violations: tuple  # trace_ids with inconsistent sampled flags

    @property
    def ok(self) -> bool:
        return not (self.missing_parents or self.duplicate_span_ids or self.sampling_violations)

    def __str__(self) -> str:
        if self.ok:
            return "integrity: OK (no missing parents, no duplicate span ids, sampling consistent)"
        lines = ["integrity: BROKEN"]
        for rec in self.missing_parents:
            lines.append(
                f"  missing parent: {rec.service}/{rec.operation} span={rec.span_id} "
                f"references absent parent={rec.parent_id}"
            )
        for span_id in self.duplicate_span_ids:
            lines.append(f"  duplicate span id: {span_id}")
        for trace_id in self.sampling_violations:
            lines.append(f"  sampling violation: trace {trace_id} has inconsistent sampled flags")
        return "\n".join(lines)


class IntegrityValidator:
    """Collects SpanRecords from all services/threads/processes and checks
    that the trace graph is complete and sane."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spans: dict[str, SpanRecord] = {}
        self._duplicates: list[tuple[SpanRecord, SpanRecord]] = []

    def record(self, rec: SpanRecord) -> None:
        with self._lock:
            existing = self._spans.get(rec.span_id)
            if existing is not None:
                self._duplicates.append((existing, rec))
            else:
                self._spans[rec.span_id] = rec

    def validate(self, trace_id: str | None = None) -> IntegrityReport:
        with self._lock:
            spans = [s for s in self._spans.values() if trace_id is None or s.trace_id == trace_id]
            known_ids = set(self._spans)
            missing = tuple(
                s for s in spans if s.parent_id is not None and s.parent_id not in known_ids
            )
            by_trace: dict[str, set] = {}
            for s in spans:
                by_trace.setdefault(s.trace_id, set()).add(s.sampled)
            sampling = tuple(sorted(t for t, flags in by_trace.items() if len(flags) > 1))
            dup_ids = tuple(sorted({a.span_id for a, _ in self._duplicates}))
        return IntegrityReport(missing, dup_ids, sampling)
