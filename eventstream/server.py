"""Server-side event log used to implement Last-Event-ID resume semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from .parser import Event, encode_event


class UnknownLastEventId(ValueError):
    """Raised when a client presents an event id outside the retained log."""


@dataclass(frozen=True)
class StoredEvent:
    position: int
    event: Event
    raw: bytes


class EventLog:
    """A retained, ordered event log with id-based replay."""

    def __init__(self) -> None:
        self._events: List[StoredEvent] = []

    def __len__(self) -> int:
        return len(self._events)

    def append(
        self,
        data: str,
        *,
        event: Optional[str] = None,
        id: Optional[str] = None,
        retry: Optional[int] = None,
    ) -> StoredEvent:
        raw = encode_event(data, event=event, id=id, retry=retry)
        stored = StoredEvent(
            position=len(self._events),
            event=Event(data=data, event=event or "message", id=id, retry=retry),
            raw=raw,
        )
        self._events.append(stored)
        return stored

    def read_after(self, last_event_id: Optional[str]) -> List[StoredEvent]:
        """Return retained events after last_event_id; None reads from the start."""

        if last_event_id is None:
            return list(self._events)
        for index in range(len(self._events) - 1, -1, -1):
            if self._events[index].event.id == last_event_id:
                return self._events[index + 1 :]
        raise UnknownLastEventId(f"unknown or expired Last-Event-ID: {last_event_id!r}")

    def encode_after(self, last_event_id: Optional[str]) -> bytes:
        return b"".join(stored.raw for stored in self.read_after(last_event_id))

    def extend(self, events: Iterable[Event]) -> None:
        for event in events:
            self.append(event.data, event=event.event, id=event.id, retry=event.retry)
