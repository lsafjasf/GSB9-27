"""Clients used in the differential and bandwidth experiments.

Byte accounting is done on serialized HTTP/1.1 messages, so the reported
savings include both the skipped response bodies and the extra bytes
spent on conditional request headers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .origin import OriginStore
from .protocol import Request, Response, handle_conditional, handle_full
from .wire import request_bytes, response_bytes


@dataclass
class CacheEntry:
    etag: str
    last_modified: str
    body: bytes
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class Stats:
    requests: int = 0
    hits: int = 0
    request_bytes: int = 0
    response_bytes: int = 0

    @property
    def total_bytes(self) -> int:
        return self.request_bytes + self.response_bytes


class NaiveClient:
    """Always issues a plain GET and always downloads the full body."""

    def __init__(self, store: OriginStore) -> None:
        self._store = store
        self.stats = Stats()

    def get(self, key: str) -> bytes:
        request = Request(key)
        response = handle_full(self._store, request)
        self.stats.requests += 1
        self.stats.request_bytes += len(request_bytes("GET", key))
        self.stats.response_bytes += len(response_bytes(response))
        return response.body or b""


class CachingClient:
    """Revalidates cached entries with ETag + Last-Modified."""

    def __init__(self, store: OriginStore) -> None:
        self._store = store
        self._cache: dict[str, CacheEntry] = {}
        self.stats = Stats()

    def get(self, key: str) -> bytes:
        entry = self._cache.get(key)
        headers: dict[str, str] = {}
        if entry is not None:
            headers["If-None-Match"] = entry.etag
            headers["If-Modified-Since"] = entry.last_modified

        request = Request(
            key,
            if_none_match=headers.get("If-None-Match"),
            if_modified_since=headers.get("If-Modified-Since"),
        )
        response = handle_conditional(self._store, request)

        self.stats.requests += 1
        self.stats.request_bytes += len(request_bytes("GET", key, headers))
        self.stats.response_bytes += len(response_bytes(response))

        if response.not_modified:
            assert entry is not None, "304 without a local cache entry"
            self.stats.hits += 1
            # Refresh validators/metadata from the 304 headers, keep local body.
            entry.etag = response.headers["ETag"]
            entry.last_modified = response.headers["Last-Modified"]
            entry.metadata = {
                name[len("X-Meta-"):]: value
                for name, value in response.headers.items()
                if name.startswith("X-Meta-")
            }
            return entry.body

        body = response.body or b""
        if response.status == 200:
            self._cache[key] = CacheEntry(
                etag=response.headers["ETag"],
                last_modified=response.headers["Last-Modified"],
                body=body,
                metadata={
                    name[len("X-Meta-"):]: value
                    for name, value in response.headers.items()
                    if name.startswith("X-Meta-")
                },
            )
        return body
