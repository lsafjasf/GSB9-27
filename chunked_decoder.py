"""Streaming decoder for HTTP/1.1 style chunked transfer coding (RFC 9112 §7.1).

Wire format per chunk:

    chunk-size [ ";" chunk-ext ] CRLF
    chunk-data                    CRLF

terminated by a zero-size chunk followed by an optional trailer section
and a final empty line:

    0 [ ";" chunk-ext ] CRLF
    *( trailer-field CRLF )
    CRLF

The decoder is fed arbitrary byte slices (network splits may fall anywhere)
and returns reassembled payload bytes.  For the same encoded byte stream,
every possible way of slicing the input produces byte-identical output.

Only the Python 3 standard library is used.
"""

from __future__ import annotations

__all__ = ["ChunkedDecodeError", "ChunkedDecoder", "encode_chunked"]

# token chars per RFC 9110 §5.6.2
_TCHAR = frozenset(b"!#$%&'*+-.^_`|~0123456789"
                   b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_HEXDIG = frozenset(b"0123456789abcdefABCDEF")
_WS = b" \t"

_STATE_SIZE = "size"
_STATE_DATA = "data"
_STATE_DATA_CRLF = "data_crlf"
_STATE_TRAILER = "trailer"
_STATE_DONE = "done"


class ChunkedDecodeError(ValueError):
    """Raised on malformed chunked input.

    ``offset`` is the absolute byte offset in the *encoded* input stream
    where the error was detected.
    """

    def __init__(self, message: str, offset: int):
        self.message = message
        self.offset = offset
        super().__init__(f"{message} (at offset {offset})")


class ChunkedDecoder:
    """Incremental chunked-transfer decoder.

    Usage::

        dec = ChunkedDecoder()
        for piece in network_pieces:
            payload += dec.feed(piece)
        payload += dec.finish()   # verifies the terminal zero chunk arrived

    Attributes:
        done:       True once the terminal zero chunk and trailers were seen.
        trailers:   List of (name, value) byte pairs from the trailer section.
        chunks:     Per-chunk metadata: list of (size, ((name, value), ...)).
    """

    def __init__(self, max_chunk_size: int = 64 * 1024 * 1024,
                 max_line: int = 8192):
        if max_chunk_size < 1:
            raise ValueError("max_chunk_size must be >= 1")
        self.max_chunk_size = max_chunk_size
        self.max_line = max_line
        self._buf = bytearray()
        self._pos = 0             # read cursor into _buf (compacted on feed)
        self._offset = 0            # absolute offset of buf[0] in the stream
        self._state = _STATE_SIZE
        self._need = 0              # payload bytes remaining in current chunk
        self._chunk_received = 0    # payload bytes already taken for current chunk
        self._chunk_size = 0
        self.trailers: list[tuple[bytes, bytes]] = []
        self.chunks: list[tuple[int, tuple]] = []

    # ------------------------------------------------------------------ API

    @property
    def done(self) -> bool:
        return self._state == _STATE_DONE

    def feed(self, data: bytes) -> bytes:
        """Feed the next slice of encoded input; return decoded payload bytes."""
        if not data:
            return b""
        if self._state == _STATE_DONE:
            raise ChunkedDecodeError(
                "trailing data after terminal chunk", self._offset)
        if self._pos:
            del self._buf[:self._pos]
            self._pos = 0
        self._buf += data
        out = bytearray()
        self._pump(out)
        return bytes(out)

    def finish(self) -> bytes:
        """Signal end of stream; raises if the message is incomplete."""
        if self._state == _STATE_SIZE:
            raise ChunkedDecodeError(
                "missing terminal zero-size chunk", self._offset)
        if self._state == _STATE_DATA:
            raise ChunkedDecodeError(
                f"stream ended inside chunk data: declared "
                f"{self._chunk_size} bytes, received {self._chunk_received}",
                self._offset)
        if self._state == _STATE_DATA_CRLF:
            raise ChunkedDecodeError(
                "stream ended expecting CRLF after chunk data", self._offset)
        if self._state == _STATE_TRAILER:
            raise ChunkedDecodeError(
                "stream ended inside trailer section", self._offset)
        return b""

    # ------------------------------------------------------------- internals

    def _advance(self, n: int) -> None:
        self._pos += n
        self._offset += n

    def _pump(self, out: bytearray) -> None:
        buf = self._buf
        while True:
            pos = self._pos
            avail = len(buf) - pos
            if self._state == _STATE_SIZE:
                idx = buf.find(b"\r\n", pos)
                if idx < 0:
                    if avail > self.max_line:
                        raise ChunkedDecodeError(
                            "chunk-size line too long", self._offset)
                    return
                line = bytes(buf[pos:idx])
                line_off = self._offset
                self._advance(idx + 2 - pos)
                size, exts = self._parse_size_line(line, line_off)
                self.chunks.append((size, exts))
                if size == 0:
                    self._state = _STATE_TRAILER
                else:
                    self._chunk_size = size
                    self._need = size
                    self._chunk_received = 0
                    self._state = _STATE_DATA
            elif self._state == _STATE_DATA:
                take = min(self._need, avail)
                if take:
                    out += buf[pos:pos + take]
                    self._advance(take)
                    self._need -= take
                    self._chunk_received += take
                if self._need:
                    return
                self._state = _STATE_DATA_CRLF
            elif self._state == _STATE_DATA_CRLF:
                if avail < 2:
                    if avail == 1 and buf[pos] != 0x0D:  # not '\r'
                        raise ChunkedDecodeError(
                            "expected CRLF after chunk data, found "
                            f"0x{buf[pos]:02x}", self._offset)
                    return
                if buf[pos] != 0x0D or buf[pos + 1] != 0x0A:
                    raise ChunkedDecodeError(
                        "expected CRLF after chunk data, found "
                        f"{bytes(buf[pos:pos + 2])!r}", self._offset)
                self._advance(2)
                self._state = _STATE_SIZE
            elif self._state == _STATE_TRAILER:
                idx = buf.find(b"\r\n", pos)
                if idx < 0:
                    if avail > self.max_line:
                        raise ChunkedDecodeError(
                            "trailer line too long", self._offset)
                    return
                line = bytes(buf[pos:idx])
                line_off = self._offset
                self._advance(idx + 2 - pos)
                if not line:
                    self._state = _STATE_DONE
                    continue
                self.trailers.append(self._parse_trailer(line, line_off))
            else:  # _STATE_DONE
                if avail:
                    raise ChunkedDecodeError(
                        "trailing data after terminal chunk", self._offset)
                return

    def _parse_size_line(self, line: bytes, base: int):
        semi = line.find(b";")
        size_part = line if semi < 0 else line[:semi]
        stripped = size_part.strip(_WS)
        lead = len(size_part) - len(size_part.lstrip(_WS))
        if not stripped:
            raise ChunkedDecodeError("empty chunk size", base + lead)
        for i, c in enumerate(stripped):
            if c not in _HEXDIG:
                raise ChunkedDecodeError(
                    f"invalid character {chr(c)!r} in chunk size",
                    base + lead + i)
        size = int(stripped, 16)
        if size > self.max_chunk_size:
            raise ChunkedDecodeError(
                f"chunk size {size} exceeds limit {self.max_chunk_size}",
                base + lead)
        exts = ()
        if semi >= 0:
            exts = self._parse_extensions(line[semi:], base + semi)
        return size, exts

    def _parse_extensions(self, rest: bytes, base: int) -> tuple:
        exts = []
        i = 0
        n = len(rest)
        while i < n:
            # rest[i] == ';' on entry
            i += 1
            while i < n and rest[i] in _WS:
                i += 1
            start = i
            while i < n and rest[i] in _TCHAR:
                i += 1
            name = rest[start:i]
            if not name:
                raise ChunkedDecodeError(
                    "expected chunk-extension name after ';'", base + i)
            value = None
            while i < n and rest[i] in _WS:
                i += 1
            if i < n and rest[i] == 0x3D:  # '='
                i += 1
                while i < n and rest[i] in _WS:
                    i += 1
                if i < n and rest[i] == 0x22:  # '"'
                    value, i = self._parse_quoted(rest, i, base)
                else:
                    start = i
                    while i < n and rest[i] in _TCHAR:
                        i += 1
                    if i == start:
                        raise ChunkedDecodeError(
                            "expected chunk-extension value after '='",
                            base + i)
                    value = rest[start:i]
            while i < n and rest[i] in _WS:
                i += 1
            if i < n and rest[i] != 0x3B:  # ';'
                raise ChunkedDecodeError(
                    f"unexpected character {chr(rest[i])!r} in chunk "
                    "extensions, expected ';'", base + i)
            exts.append((name, value))
        return tuple(exts)

    @staticmethod
    def _parse_quoted(rest: bytes, i: int, base: int):
        # rest[i] == '"'
        i += 1
        val = bytearray()
        n = len(rest)
        while True:
            if i >= n:
                raise ChunkedDecodeError(
                    "unterminated quoted-string in chunk extension", base + i)
            c = rest[i]
            if c == 0x5C:  # backslash
                i += 1
                if i >= n:
                    raise ChunkedDecodeError(
                        "unterminated quoted-pair in chunk extension",
                        base + i)
                val.append(rest[i])
                i += 1
            elif c == 0x22:  # closing '"'
                return bytes(val), i + 1
            elif 0x20 <= c < 0x7F:
                val.append(c)
                i += 1
            else:
                raise ChunkedDecodeError(
                    f"invalid character 0x{c:02x} in quoted-string", base + i)

    def _parse_trailer(self, line: bytes, base: int):
        colon = line.find(b":")
        if colon <= 0:
            raise ChunkedDecodeError(
                "malformed trailer field, expected 'Name: value'",
                base + (colon if colon >= 0 else 0))
        name = line[:colon]
        for i, c in enumerate(name):
            if c not in _TCHAR:
                raise ChunkedDecodeError(
                    f"invalid character {chr(c)!r} in trailer field name",
                    base + i)
        value = line[colon + 1:].strip(_WS)
        return name, value


def encode_chunked(data: bytes, chunk_size: int = 8192,
                   trailers: tuple = ()) -> bytes:
    """Encode *data* as a chunked stream (helper for tests/benchmarks)."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    out = bytearray()
    for pos in range(0, len(data), chunk_size):
        piece = data[pos:pos + chunk_size]
        out += f"{len(piece):x}\r\n".encode("ascii")
        out += piece
        out += b"\r\n"
    out += b"0\r\n"
    for name, value in trailers:
        out += name + b": " + value + b"\r\n"
    out += b"\r\n"
    return bytes(out)
