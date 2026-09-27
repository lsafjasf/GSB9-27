"""Streaming HTTP chunked transfer-encoding decoder (RFC 9112 §7.1).

Pure standard library. Feed arbitrary byte slices; get back decoded bytes.
All framing errors raise ChunkedDecodeError carrying the absolute stream
offset at which the problem was detected.
"""

import re

__all__ = ["ChunkedDecodeError", "ChunkedDecoder", "decode"]

_TOKEN_CLASS = rb"[!#$%&'*+\-.^_`|~0-9A-Za-z]"
_QD = rb"(?:[^\"\\\r\n]|\\[\t\x20-\x7e])"
_CHUNK_EXT_RE = re.compile(
    rb"(?:;" + _TOKEN_CLASS + rb"+(?:=(?:" + _TOKEN_CLASS + rb"+|\"" + _QD + rb"*\"))?)*$"
)
_SIZE_PREFIX_RE = re.compile(rb"[0-9A-Fa-f]+")
_TRAILER_RE = re.compile(rb"^" + _TOKEN_CLASS + rb"+:[\t ]?[^\r\n]*$")


class ChunkedDecodeError(ValueError):
    """Decoding failure. `offset` is the absolute byte offset in the stream."""

    def __init__(self, message, offset):
        super().__init__(f"{message} (at offset {offset})")
        self.message = message
        self.offset = offset


class ChunkedDecoder:
    """Incremental chunked-encoding decoder.

    Usage:
        d = ChunkedDecoder()
        out = d.feed(piece)      # -> decoded bytes (may be b"")
        ...
        d.finish()               # raises if stream ended mid-message
    """

    MAX_LINE = 65536  # sanity bound for size / trailer lines

    def __init__(self, max_chunk_size=None):
        self.max_chunk_size = max_chunk_size  # optional declared-size limit
        self._buf = bytearray()
        self._pos = 0           # read cursor inside self._buf
        self._base = 0          # absolute offset of self._buf[0]
        self._state = "size"    # size | data | data_crlf | trailer | done
        self._remaining = 0
        self.done = False

    @property
    def offset(self):
        """Absolute offset of the next unconsumed input byte."""
        return self._base

    def feed(self, data):
        if isinstance(data, str):
            raise TypeError("feed() requires bytes")
        if self.done:
            if data:
                raise ChunkedDecodeError("data after terminal zero-size chunk", self._base)
            return b""
        if self._pos == len(self._buf):  # fully consumed: reuse the buffer
            self._buf.clear()
            self._pos = 0
        self._buf += data
        out = bytearray()
        self._pump(out)
        return bytes(out)

    def finish(self):
        if not self.done:
            raise ChunkedDecodeError(
                "unexpected end of stream: missing terminal zero-size chunk", self._base
            )

    # ------------------------------------------------------------------
    def _consume(self, n):
        self._pos += n
        self._base += n
        # compact occasionally so the buffer does not grow without bound
        if self._pos >= 65536 and self._pos * 2 >= len(self._buf):
            del self._buf[:self._pos]
            self._pos = 0

    def _pending(self):
        return len(self._buf) - self._pos

    def _pump(self, out):
        buf, pos = self._buf, self._pos
        while True:
            if self._state == "size":
                idx = buf.find(b"\r\n", self._pos)
                if idx < 0:
                    if self._pending() > self.MAX_LINE:
                        raise ChunkedDecodeError("chunk-size line too long", self._base)
                    return
                size = self._parse_size_line(bytes(buf[self._pos:idx]))
                self._consume(idx + 2 - self._pos)
                if size == 0:
                    self._state = "trailer"
                else:
                    if self.max_chunk_size is not None and size > self.max_chunk_size:
                        raise ChunkedDecodeError(
                            f"declared chunk size {size} exceeds limit {self.max_chunk_size}",
                            self._base,
                        )
                    self._remaining = size
                    self._state = "data"
            elif self._state == "data":
                avail = self._pending()
                if not avail:
                    return
                take = min(self._remaining, avail)
                out += buf[self._pos:self._pos + take]
                self._consume(take)
                self._remaining -= take
                if self._remaining:
                    return
                self._state = "data_crlf"
            elif self._state == "data_crlf":
                if self._pending() < 2:
                    return
                if buf[self._pos] != 0x0D or buf[self._pos + 1] != 0x0A:
                    raise ChunkedDecodeError(
                        "chunk size does not match actual data: expected CRLF after chunk data",
                        self._base,
                    )
                self._consume(2)
                self._state = "size"
            elif self._state == "trailer":
                idx = buf.find(b"\r\n", self._pos)
                if idx < 0:
                    if self._pending() > self.MAX_LINE:
                        raise ChunkedDecodeError("trailer line too long", self._base)
                    return
                if idx == self._pos:  # empty line terminates the trailer section
                    self._consume(2)
                    self._state = "done"
                    self.done = True
                    return
                line = bytes(buf[self._pos:idx])
                if not _TRAILER_RE.match(line):
                    raise ChunkedDecodeError("malformed trailer header", self._base)
                self._consume(idx + 2 - self._pos)
            else:  # done
                return

    def _parse_size_line(self, line):
        m = _SIZE_PREFIX_RE.match(line)
        if not m:
            raise ChunkedDecodeError(
                "invalid chunk length: expected hexadecimal size", self._base
            )
        rest = line[m.end():]
        if not _CHUNK_EXT_RE.match(rest):
            raise ChunkedDecodeError(
                "malformed chunk extension", self._base + m.end()
            )
        return int(m.group(0), 16)


def decode(data, max_chunk_size=None):
    """One-shot decode of a complete chunked stream."""
    d = ChunkedDecoder(max_chunk_size=max_chunk_size)
    out = d.feed(data)
    d.finish()
    return out
