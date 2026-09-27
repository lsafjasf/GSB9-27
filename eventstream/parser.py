"""Incremental parser and encoder for Server-Sent Events style streams."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List, Optional, Union


BytesLike = Union[bytes, bytearray, memoryview]
_DATA_LINE_SPLIT = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True)
class Event:
    """A dispatched application event."""

    data: str
    event: str = "message"
    id: Optional[str] = None
    retry: Optional[int] = None


class SSEParser:
    """Incrementally parses an SSE byte stream.

    The parser accepts arbitrarily split chunks. Comment lines (including
    heartbeats) and unknown fields are ignored. A blank line dispatches an
    event only when at least one data field has been seen.
    """

    def __init__(
        self,
        *,
        encoding: str = "utf-8",
        errors: str = "replace",
        max_buffer_size: Optional[int] = None,
    ) -> None:
        if max_buffer_size is not None and max_buffer_size <= 0:
            raise ValueError("max_buffer_size must be positive or None")
        self.encoding = encoding
        self.errors = errors
        self.max_buffer_size = max_buffer_size
        self._buffer = bytearray()
        self._data_buffer: List[str] = []
        self._event_type_buffer = ""
        self._last_event_id: Optional[str] = None
        self._retry: Optional[int] = None

    @property
    def last_event_id(self) -> Optional[str]:
        """The most recently seen valid id field, including uncommitted ids."""

        return self._last_event_id

    @property
    def retry(self) -> Optional[int]:
        """The most recently seen valid retry field, in milliseconds."""

        return self._retry

    def feed(self, chunk: BytesLike) -> List[Event]:
        """Feed bytes and return events completed by this chunk."""

        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("chunk must be bytes-like")
        incoming = bytes(chunk)
        if (
            self.max_buffer_size is not None
            and len(self._buffer) + len(incoming) > self.max_buffer_size
        ):
            raise BufferError("incomplete line exceeds max_buffer_size")
        self._buffer.extend(incoming)

        events: List[Event] = []
        while True:
            line = self._pop_line()
            if line is None:
                break
            event = self._process_line(line.decode(self.encoding, self.errors))
            if event is not None:
                events.append(event)
        return events

    def close(self) -> List[Event]:
        """Finish the stream and dispatch any complete pending event.

        EOF terminates a pending line and, when data fields were present, a
        pending event. This mirrors SSE behavior and keeps early server closes
        deterministic instead of raising a parser error.
        """

        events: List[Event] = []
        if self._buffer:
            pending = bytes(self._buffer)
            if pending.endswith(b"\r"):
                pending = pending[:-1]
            line = pending.decode(self.encoding, self.errors)
            self._buffer.clear()
            event = self._process_line(line)
            if event is not None:
                events.append(event)
        event = self._dispatch_event()
        if event is not None:
            events.append(event)
        return events

    def _pop_line(self) -> Optional[bytes]:
        cr_index = self._buffer.find(b"\r")
        lf_index = self._buffer.find(b"\n")

        if cr_index == -1 and lf_index == -1:
            return None
        if cr_index != -1 and (lf_index == -1 or cr_index < lf_index):
            if cr_index == len(self._buffer) - 1:
                return None
            if self._buffer[cr_index + 1] == 0x0A:
                line = bytes(self._buffer[:cr_index])
                del self._buffer[: cr_index + 2]
            else:
                line = bytes(self._buffer[:cr_index])
                del self._buffer[: cr_index + 1]
            return line

        line = bytes(self._buffer[:lf_index])
        del self._buffer[: lf_index + 1]
        return line

    def _process_line(self, line: str) -> Optional[Event]:
        if line.startswith(":"):
            return None

        if ":" in line:
            field, value = line.split(":", 1)
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""

        if field == "data":
            self._data_buffer.append(value)
        elif field == "event":
            self._event_type_buffer = value
        elif field == "id":
            if "\0" not in value:
                self._last_event_id = value
        elif field == "retry":
            if value.isascii() and value.isdigit():
                self._retry = int(value)
        elif field == "":
            return self._dispatch_event()
        return None

    def _dispatch_event(self) -> Optional[Event]:
        if not self._data_buffer:
            self._event_type_buffer = ""
            return None

        event = Event(
            data="\n".join(self._data_buffer),
            event=self._event_type_buffer or "message",
            id=self._last_event_id,
            retry=self._retry,
        )
        self._data_buffer.clear()
        self._event_type_buffer = ""
        return event


def encode_event(
    data: str,
    *,
    event: Optional[str] = None,
    id: Optional[str] = None,
    retry: Optional[int] = None,
) -> bytes:
    """Encode one event using LF line endings."""

    lines: List[str] = []
    if event is not None:
        _validate_single_line("event", event)
        lines.append(f"event: {event}")
    if id is not None:
        _validate_single_line("id", id)
        if "\0" in id:
            raise ValueError("id must not contain NUL")
        lines.append(f"id: {id}")
    if retry is not None:
        if not isinstance(retry, int) or retry < 0:
            raise ValueError("retry must be a non-negative integer")
        lines.append(f"retry: {retry}")
    for data_line in _DATA_LINE_SPLIT.split(data):
        lines.append(f"data: {data_line}")
    lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def heartbeat(comment: str = "heartbeat") -> bytes:
    """Encode a comment-only heartbeat, which never dispatches an event."""

    _validate_single_line("comment", comment)
    return f": {comment}\n\n".encode("utf-8")


def _validate_single_line(name: str, value: str) -> None:
    if "\r" in value or "\n" in value:
        raise ValueError(f"{name} must be a single line")
