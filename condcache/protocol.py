"""Conditional request semantics over :class:`OriginStore`.

Implemented validation rules (RFC 9110):

* ``If-None-Match`` is evaluated first; whenever the header is present
  (even malformed), ``If-Modified-Since`` is ignored.
* ``If-None-Match: *`` matches any existing representation.
* A malformed entity-tag invalidates the whole header -> no match -> 200.
* ``If-Modified-Since`` matches at one-second resolution: a resource not
  modified *after* the supplied instant yields 304.
* A malformed date is ignored -> 200.
* 304 responses carry no body but repeat validator headers, so a cache
  can refresh ETag/Last-Modified (and metadata) without re-downloading.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import validators
from .origin import OriginStore, Revision

_STATUS_REASONS = {
    200: "OK",
    304: "Not Modified",
    404: "Not Found",
    405: "Method Not Allowed",
}


@dataclass
class Request:
    key: str
    method: str = "GET"
    if_none_match: str | None = None
    if_modified_since: str | None = None


@dataclass
class Response:
    status: int
    body: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def reason(self) -> str:
        return _STATUS_REASONS[self.status]

    @property
    def not_modified(self) -> bool:
        return self.status == 304


def _validator_headers(revision: Revision) -> dict[str, str]:
    headers = {
        "ETag": revision.etag,
        "Last-Modified": revision.last_modified,
    }
    for name, value in revision.metadata.items():
        headers[f"X-Meta-{name}"] = value
    return headers


def _full(revision: Revision, method: str) -> Response:
    if method == "HEAD":
        return Response(
            200,
            b"",
            {**_validator_headers(revision), "Content-Length": str(len(revision.body))},
        )
    return Response(200, revision.body, _validator_headers(revision))


def _not_modified(revision: Revision) -> Response:
    return Response(304, b"", _validator_headers(revision))


def handle_full(store: OriginStore, request: Request) -> Response:
    """Naive unconditional handler: every hit is a full 200 response."""
    revision = store.get(request.key)
    if revision is None:
        return Response(404, b"Not Found", {"Content-Type": "text/plain"})
    if request.method not in ("GET", "HEAD"):
        return Response(405, b"Method Not Allowed", {"Content-Type": "text/plain"})
    return _full(revision, request.method)


def handle_conditional(store: OriginStore, request: Request) -> Response:
    """Evaluate cache validators and return 200 / 304 / 4xx."""
    if request.method not in ("GET", "HEAD"):
        return Response(405, b"Method Not Allowed", {"Content-Type": "text/plain"})

    revision = store.get(request.key)
    if revision is None:
        return Response(404, b"Not Found", {"Content-Type": "text/plain"})

    inm_present = request.if_none_match is not None
    if inm_present:
        members = validators.parse_if_none_match(request.if_none_match)
        # Malformed header -> empty list -> never matches, and IMS is skipped.
        if members and validators.any_match(members, revision.etag):
            return _not_modified(revision)
        return _full(revision, request.method)

    ims = validators.parse_http_date(request.if_modified_since)
    if ims is not None:
        # HTTP dates have 1-second resolution; mtime is stored as whole seconds.
        if revision.mtime <= ims:
            return _not_modified(revision)
    return _full(revision, request.method)
