"""Durable session state store.

Persistence rules:
- Atomic write: serialize to a temp file, fsync, os.replace(), fsync the
  directory. A reader never observes a half-written state file.
- Every document carries a SHA-256 checksum of its payload. A torn or
  tampered write that somehow lands is detected on load.
- Schema version is checked on load; mismatches refuse to run instead of
  guessing (no silent migration, no blind re-execution).
- Corruption handling is fail-closed: the bad file is quarantined aside and
  CorruptStateError is raised. Effects are NOT re-run, preserving
  at-most-once semantics.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time

SCHEMA_VERSION = 1
STATE_FILENAME = "state.json"


class StateError(Exception):
    """Base class for state store errors."""


class CorruptStateError(StateError):
    """State file is unparseable or fails its checksum."""


class StateVersionError(StateError):
    """State file was written by an incompatible schema version."""


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _checksum(payload) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


class StateStore:
    def __init__(self, state_dir: str, session_id: str):
        self.state_dir = state_dir
        self.session_id = session_id
        self.path = os.path.join(state_dir, STATE_FILENAME)
        os.makedirs(state_dir, exist_ok=True)

    def load(self) -> dict:
        """Load state, or return a fresh document if none exists.

        Raises CorruptStateError (fail-closed, file quarantined) on torn or
        tampered data, and StateVersionError on schema mismatch.
        """
        try:
            with open(self.path, "rb") as fh:
                raw = fh.read()
        except FileNotFoundError:
            return self._fresh()

        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise self._quarantine(raw, f"unparseable state ({exc})")
        if not isinstance(doc, dict) or "payload" not in doc or "checksum" not in doc:
            raise self._quarantine(raw, "missing payload/checksum envelope")

        payload = doc["payload"]
        if _checksum(payload) != doc["checksum"]:
            raise self._quarantine(raw, "checksum mismatch (torn or tampered write)")

        version = payload.get("version")
        if version != SCHEMA_VERSION:
            raise StateVersionError(
                f"state schema version {version!r} != supported {SCHEMA_VERSION}; "
                "refusing to run (no auto-migration, no blind re-execution)"
            )
        if payload.get("session_id") != self.session_id:
            raise StateError(
                f"state belongs to session {payload.get('session_id')!r}, "
                f"not {self.session_id!r}"
            )
        return payload

    def save(self, payload: dict) -> None:
        """Atomically and durably persist the payload."""
        doc = {"payload": payload, "checksum": _checksum(payload)}
        data = _canonical(doc)
        fd, tmp = tempfile.mkstemp(dir=self.state_dir, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            self._fsync_dir()
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _fresh(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "effects": {},
            "steps": {},
        }

    def _fsync_dir(self) -> None:
        fd = os.open(self.state_dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _quarantine(self, raw: bytes, reason: str) -> CorruptStateError:
        qpath = f"{self.path}.corrupt-{time.time_ns()}"
        with open(qpath, "wb") as fh:
            fh.write(raw)
        return CorruptStateError(
            f"{reason}; quarantined to {qpath}; "
            "failing closed: effects will NOT be re-run"
        )
