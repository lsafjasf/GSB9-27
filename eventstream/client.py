"""Resumable client-side event consumption with bounded duplicate suppression."""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, ContextManager, Optional, Protocol, TypeVar

from .parser import Event, SSEParser


EventCallback = Callable[[Event], None]
T = TypeVar("T")


class Stream(Protocol):
    def read(self, size: int = -1) -> bytes:
        ...


class Transport(Protocol):
    def open(self, headers: dict[str, str]) -> ContextManager[Stream]:
        ...


class EventStreamClient:
    """Consumes events and tracks the last committed event id.

    Duplicate suppression is keyed by non-empty event id. The cache is bounded
    and evicts the oldest ids, so memory stays O(dedup_size), not O(stream).
    """

    def __init__(self, *, dedup_size: int = 1024, chunk_size: int = 65536) -> None:
        if dedup_size <= 0:
            raise ValueError("dedup_size must be positive")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.dedup_size = dedup_size
        self.chunk_size = chunk_size
        self.last_event_id: Optional[str] = None
        self._seen_ids: OrderedDict[str, None] = OrderedDict()

    def request_headers(self) -> dict[str, str]:
        """Headers to send when opening or reopening the stream."""

        if self.last_event_id is None:
            return {}
        return {"Last-Event-ID": self.last_event_id}

    def consume(self, transport: Transport, on_event: EventCallback) -> int:
        """Consume one connection and return the number of new events delivered.

        Transport failures are deliberately propagated. The caller can catch
        them and call consume again; request_headers() will contain the last
        committed event id.
        """

        delivered = 0
        parser = SSEParser()
        with transport.open(self.request_headers()) as stream:
            while True:
                chunk = stream.read(self.chunk_size)
                if not chunk:
                    break
                for event in parser.feed(chunk):
                    delivered += self._deliver(event, on_event)
            for event in parser.close():
                delivered += self._deliver(event, on_event)
        return delivered

    def _deliver(self, event: Event, on_event: EventCallback) -> int:
        event_id = event.id
        if event_id:
            if event_id in self._seen_ids:
                return 0
            self._seen_ids[event_id] = None
            self._seen_ids.move_to_end(event_id)
            while len(self._seen_ids) > self.dedup_size:
                self._seen_ids.popitem(last=False)
            self.last_event_id = event_id
        on_event(event)
        return 1
