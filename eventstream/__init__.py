"""Small, standard-library-only event stream toolkit."""

from .client import EventStreamClient, Stream, Transport
from .parser import Event, SSEParser, encode_event, heartbeat
from .server import EventLog, StoredEvent, UnknownLastEventId

__all__ = [
    "Event",
    "EventLog",
    "EventStreamClient",
    "SSEParser",
    "StoredEvent",
    "Stream",
    "Transport",
    "UnknownLastEventId",
    "encode_event",
    "heartbeat",
]
