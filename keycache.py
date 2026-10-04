"""Versioned key fetching and caching library (Python 3 standard library only).

Design rules:
- Cached keys carry a version and an expiry time. A key is expired when
  ``now >= expires_at`` (expiry instant is exclusive), so "exactly at
  expiry" already counts as expired and triggers a refetch.
- When the cache is expired (or missing), the key is refetched from an
  external source through an injected fetcher callable.
- If a fetch fails but the cached key is still unexpired, the stale key
  keeps being served (degraded mode). The degradation window is the
  remaining lifetime of the cached key: ``expires_at - now``.
- Key versions must be monotonic. A fetched version lower than the
  highest version ever accepted is a rollback: it is rejected, recorded,
  and never served.
- Time is injectable via the ``now`` callable, which makes polling,
  expiry and degradation fully deterministic in tests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, List, Optional


class KeyCacheError(Exception):
    """Base error for the key cache."""


class NoUsableKeyError(KeyCacheError):
    """No usable key: cache expired/missing and the fetch did not succeed."""


@dataclass(frozen=True)
class KeyMaterial:
    """A key fetched from the external source."""

    key: str
    version: int
    expires_at: float  # epoch seconds; valid while now < expires_at

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass(frozen=True)
class CacheEvent:
    """Audit record. kind is one of:

    accepted  - a fetched key passed validation and became the cache entry
    rejected  - a fetched key was refused (rollback or invalid payload)
    degraded  - fetch failed; an unexpired cached key was served instead
    expired   - fetch failed and no unexpired key was available
    """

    kind: str
    at: float
    version: Optional[int]
    reason: str


class KeyCache:
    def __init__(
        self,
        fetcher: Callable[[], KeyMaterial],
        now: Callable[[], float] = time.time,
        refresh_margin: float = 0.0,
    ) -> None:
        self._fetcher = fetcher
        self._now = now
        self._refresh_margin = refresh_margin
        self._cached: Optional[KeyMaterial] = None
        self._max_version: Optional[int] = None
        self.events: List[CacheEvent] = []

    # -- public API -----------------------------------------------------

    def get_key(self) -> KeyMaterial:
        """Return a usable key, refetching when the cache is due."""
        now = self._now()
        if self._cached is None or self._due(self._cached, now):
            self.refresh()
        assert self._cached is not None  # refresh() either sets it or raises
        return self._cached

    def refresh(self) -> Optional[KeyMaterial]:
        """Fetch a fresh key from the external source.

        Returns the active key after the attempt (the newly accepted one,
        or the still-valid cached one in degraded mode). Raises
        ``NoUsableKeyError`` when nothing usable remains.
        """
        now = self._now()
        try:
            fetched = self._fetcher()
        except Exception as exc:  # fetch failure -> degrade if possible
            if self._cached is not None and not self._cached.is_expired(now):
                self.events.append(
                    CacheEvent(
                        "degraded",
                        now,
                        self._cached.version,
                        "fetch failed (%r); serving cached key v%d, "
                        "degradation window %.0fs"
                        % (exc, self._cached.version, self.degradation_window()),
                    )
                )
                return self._cached
            self.events.append(
                CacheEvent(
                    "expired",
                    now,
                    None,
                    "fetch failed (%r) and no unexpired key available" % (exc,),
                )
            )
            raise NoUsableKeyError(
                "fetch failed and no unexpired key available"
            ) from exc

        reason = self._validate(fetched)
        if reason is not None:
            self.events.append(CacheEvent("rejected", now, fetched.version, reason))
            if self._cached is not None and not self._cached.is_expired(now):
                return self._cached
            raise NoUsableKeyError("fetched key rejected: " + reason)

        self._cached = fetched
        self._max_version = fetched.version
        self.events.append(CacheEvent("accepted", now, fetched.version, "ok"))
        return fetched

    def degradation_window(self) -> float:
        """Seconds the cached key can still be served if fetches fail."""
        if self._cached is None:
            return 0.0
        return max(0.0, self._cached.expires_at - self._now())

    @property
    def version_history(self) -> List[int]:
        """Sequence of accepted key versions, in order."""
        return [e.version for e in self.events if e.kind == "accepted"]  # type: ignore[misc]

    @property
    def rejections(self) -> List[CacheEvent]:
        return [e for e in self.events if e.kind == "rejected"]

    @property
    def cached(self) -> Optional[KeyMaterial]:
        return self._cached

    # -- internals ------------------------------------------------------

    def _due(self, key: KeyMaterial, now: float) -> bool:
        return now >= key.expires_at - self._refresh_margin

    def _validate(self, key: KeyMaterial) -> Optional[str]:
        """Return a rejection reason, or None when the key is acceptable."""
        if not key.key:
            return "empty key material"
        if key.is_expired(self._now()):
            return "fetched key already expired"
        if self._max_version is not None and key.version < self._max_version:
            return (
                "version rollback: fetched v%d < current v%d"
                % (key.version, self._max_version)
            )
        return None
