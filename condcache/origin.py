"""Thread-safe origin key/value store.

Each write replaces the resource with an immutable :class:`Revision`.
Reads return the snapshot taken under the lock, so a request can never
observe a torn state where ETag and body come from different writes.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Mapping

from . import validators


@dataclass(frozen=True)
class Revision:
    """One immutable resource revision."""

    body: bytes
    etag: str
    mtime: float  # POSIX seconds, monotonically non-decreasing per key
    metadata: Mapping[str, str]

    @property
    def last_modified(self) -> str:
        return validators.http_date(self.mtime)


class OriginStore:
    """In-memory origin server storage, safe for concurrent writers."""

    def __init__(self, clock=time.time) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Revision] = {}
        self._clock = clock

    def _next_mtime(self, key: str) -> float:
        now = validators.truncated_to_seconds(self._clock())
        previous = self._data.get(key)
        if previous is not None and now <= previous.mtime:
            return previous.mtime + 1.0
        return now

    def put(self, key: str, body: bytes, metadata: Mapping[str, str] | None = None) -> Revision:
        """Create or replace a resource and return its new revision."""
        if isinstance(body, str):
            body = body.encode("utf-8")
        meta = dict(metadata or {})
        with self._lock:
            revision = Revision(
                body=body,
                etag=validators.make_etag(body),
                mtime=self._next_mtime(key),
                metadata=meta,
            )
            self._data[key] = revision
            return revision

    def touch(self, key: str, metadata: Mapping[str, str]) -> Revision:
        """Replace metadata only; body and ETag stay identical, mtime advances.

        Models "content identical, metadata changed".
        """
        with self._lock:
            current = self._data.get(key)
            if current is None:
                raise KeyError(key)
            revision = Revision(
                body=current.body,
                etag=current.etag,
                mtime=self._next_mtime(key),
                metadata=dict(metadata),
            )
            self._data[key] = revision
            return revision

    def get(self, key: str) -> Revision | None:
        """Return the current immutable revision, or ``None`` if absent."""
        with self._lock:
            return self._data.get(key)
