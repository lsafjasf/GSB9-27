"""Minimal HTTP/1.1 wire serialization (standard library only).

Working on the exact byte representation lets the bandwidth comparison be
measured rather than guessed.
"""

from __future__ import annotations

from .protocol import Response

CRLF = "\r\n"


def request_bytes(method: str, key: str, headers: dict[str, str] | None = None) -> bytes:
    """Serialize one request line plus headers (Host is always present)."""
    lines = [f"{method} /{key} HTTP/1.1", "Host: origin"]
    for name, value in (headers or {}).items():
        lines.append(f"{name}: {value}")
    return (CRLF.join(lines) + CRLF + CRLF).encode("ascii")


def response_bytes(response: Response, include_body: bool = True) -> bytes:
    """Serialize a response. ``include_body=False`` models a HEAD reply."""
    head = [f"HTTP/1.1 {response.status} {response.reason}"]
    has_length = any(name.lower() == "content-length" for name in response.headers)
    for name, value in response.headers.items():
        head.append(f"{name}: {value}")
    if response.body is not None and not has_length:
        head.append(f"Content-Length: {len(response.body)}")
    raw = (CRLF.join(head) + CRLF + CRLF).encode("utf-8")
    if include_body and response.body is not None:
        raw += response.body
    return raw
