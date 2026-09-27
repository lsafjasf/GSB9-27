"""Persistent, bounded token revocation list (Python 3 stdlib only).

Design
------
- Each revocation record maps a token id (jti) to the token's own natural
  expiry timestamp. A record only needs to live as long as the token itself
  could still be presented, which gives the store a natural upper bound.
- Persistence is a single JSON snapshot written atomically (tmp + rename),
  so a crash mid-write never corrupts the previous state.
- Time is injectable (`now` callable) so tests control the clock.

Verdicts
--------
- NOT_REVOKED: token is still within its natural lifetime and no live
  revocation record exists.
- REVOKED: a live revocation record exists for the jti.
- UNKNOWN: the token's natural lifetime has ended and no record exists.
  The record (if any) may already have been purged, so revocation cannot
  be determined. Policy: an expired token must be rejected by the caller
  anyway, so UNKNOWN is always safe to treat as "reject".
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from enum import Enum
from typing import Callable, Dict, Iterable, Optional, Tuple


class Verdict(Enum):
    NOT_REVOKED = "not_revoked"
    REVOKED = "revoked"
    UNKNOWN = "unknown"


class RevocationList:
    def __init__(
        self,
        path: Optional[str] = None,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        self._path = path
        self._now: Callable[[], float] = now if now is not None else time.time
        self._records: Dict[str, float] = {}
        self._lock = threading.RLock()
        if path is not None:
            self.load()

    # ---- mutation ------------------------------------------------------

    def revoke(self, jti: str, exp: float) -> bool:
        """Revoke one token until its natural expiry `exp`.

        Returns True if the store changed. Re-revoking the same jti is
        idempotent; if a different expiry is supplied the later (larger)
        one wins so the record never shrinks.
        """
        with self._lock:
            old = self._records.get(jti)
            if old is not None and old >= exp:
                return False
            self._records[jti] = float(exp)
            return True

    def revoke_many(self, items: Iterable[Tuple[str, float]]) -> int:
        """Batch revoke; returns number of records actually changed."""
        changed = 0
        with self._lock:
            for jti, exp in items:
                if self.revoke(jti, exp):
                    changed += 1
        return changed

    def purge(self) -> int:
        """Drop records whose token expiry has passed. Returns count removed.

        Safe under clock rollback: only records with exp <= current time
        are removed, so a backwards clock simply purges less.
        """
        now = self._now()
        with self._lock:
            expired = sum(1 for e in self._records.values() if e <= now)
            if expired:
                # Rebuild instead of deleting in place: CPython dicts never
                # shrink on del, so this actually releases the memory.
                self._records = {
                    j: e for j, e in self._records.items() if e > now
                }
        return expired

    # ---- query ---------------------------------------------------------

    def check(self, jti: str, exp: float) -> Verdict:
        now = self._now()
        with self._lock:
            rec = self._records.get(jti)
        if rec is not None and rec > now:
            return Verdict.REVOKED
        if exp > now:
            # Token still naturally valid and no live record -> clean.
            return Verdict.NOT_REVOKED
        # Token lifetime over and no live record: cannot determine.
        return Verdict.UNKNOWN

    # ---- persistence ---------------------------------------------------

    def save(self) -> None:
        if self._path is None:
            return
        with self._lock:
            payload = json.dumps(
                dict(sorted(self._records.items())), separators=(",", ":")
            )
        directory = os.path.dirname(os.path.abspath(self._path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".revoke-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load(self) -> None:
        if self._path is None or not os.path.exists(self._path):
            return
        with open(self._path, "r") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("corrupt revocation store: expected object")
        with self._lock:
            self._records = {str(k): float(v) for k, v in data.items()}

    # ---- introspection -------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def memory_stats(self) -> dict:
        """Approximate in-memory footprint of the record table (bytes)."""
        with self._lock:
            size = sys.getsizeof(self._records)
            for k, v in self._records.items():
                size += sys.getsizeof(k) + sys.getsizeof(v)
            return {"records": len(self._records), "approx_bytes": size}

    def snapshot(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._records)
