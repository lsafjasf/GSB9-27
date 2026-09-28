"""Chunked, compressed, verifiable snapshot store (Python 3 stdlib only).

Container layout
----------------
    [ file header ][ compressed chunk 0 ][ chunk 1 ] ... [ index ][ trailer ]

File header (16 bytes):
    magic    b"SNAPSHOT" (8 bytes)
    version  uint16 BE
    flags    uint16 BE (reserved, 0)
    reserved 4 zero bytes

Every chunk is a raw zlib stream (wbits=15).

Index = UTF-8 JSON:
    {"chunk_size": int,
     "chunks": [{"offset", "compressed_size", "size", "sha256"}, ...]}
    sha256 covers the UNCOMPRESSED chunk bytes.

Trailer (48 bytes, final bytes of the file):
    index_offset  uint64 BE
    index_length  uint64 BE
    index_sha256  32 raw bytes (digest of the serialized index)
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import zlib
from typing import BinaryIO, Iterable, List, Optional, Sequence, Union

MAGIC = b"SNAPSHOT"
VERSION = 1
_HEADER = struct.Struct(">8sHH4x")
HEADER_SIZE = _HEADER.size          # 16
_TRAILER = struct.Struct(">QQ32s")
TRAILER_SIZE = _TRAILER.size        # 48


class SnapshotError(Exception):
    """Base class for snapshot container errors."""


class CorruptSnapshotError(SnapshotError):
    """Container structure or index metadata is corrupt."""


class CorruptChunkError(CorruptSnapshotError):
    """One specific chunk failed decompression or checksum verification."""

    def __init__(self, index: int, reason: str):
        super().__init__("chunk %d corrupt: %s" % (index, reason))
        self.index = index
        self.reason = reason


class MissingReplicaChunkError(SnapshotError):
    """No healthy replica could supply a requested chunk."""


class ChunkRecord:
    __slots__ = ("offset", "compressed_size", "size", "sha256")

    def __init__(self, offset: int, compressed_size: int, size: int, sha256: str):
        self.offset = offset
        self.compressed_size = compressed_size
        self.size = size
        self.sha256 = sha256

    def to_dict(self) -> dict:
        return {"offset": self.offset, "compressed_size": self.compressed_size,
                "size": self.size, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, d: dict) -> "ChunkRecord":
        try:
            return cls(int(d["offset"]), int(d["compressed_size"]),
                       int(d["size"]), str(d["sha256"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptSnapshotError("bad chunk index record: %r" % (d,)) from exc


def _iter_chunks(source: Union[bytes, BinaryIO], chunk_size: int):
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if isinstance(source, (bytes, bytearray, memoryview)):
        view = memoryview(source)
        for start in range(0, len(view), chunk_size):
            yield bytes(view[start:start + chunk_size])
        return
    while True:
        block = source.read(chunk_size)
        if not block:
            return
        yield block


def write_snapshot(
    path: Union[str, os.PathLike],
    source: Union[bytes, BinaryIO],
    chunk_size: int = 64 * 1024,
    level: int = zlib.Z_DEFAULT_COMPRESSION,
) -> dict:
    """Compress ``source`` into a chunked snapshot written atomically to ``path``."""
    if not (0 <= level <= 9 or level == zlib.Z_DEFAULT_COMPRESSION):
        raise ValueError("level must be 0..9 or zlib.Z_DEFAULT_COMPRESSION")

    tmp_path = os.fspath(path) + ".tmp"
    records: List[ChunkRecord] = []
    offset = HEADER_SIZE
    original_size = 0

    with open(tmp_path, "wb") as out:
        out.write(_HEADER.pack(MAGIC, VERSION, 0))
        for block in _iter_chunks(source, chunk_size):
            payload = zlib.compress(block, level)
            records.append(ChunkRecord(offset, len(payload), len(block),
                                       hashlib.sha256(block).hexdigest()))
            out.write(payload)
            offset += len(payload)
            original_size += len(block)

        index_offset = offset
        index_bytes = json.dumps(
            {"chunk_size": chunk_size, "chunks": [r.to_dict() for r in records]},
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        out.write(index_bytes)
        out.write(_TRAILER.pack(index_offset, len(index_bytes),
                                hashlib.sha256(index_bytes).digest()))

    os.replace(tmp_path, path)
    return {
        "chunks": len(records),
        "original_size": original_size,
        "compressed_size": os.path.getsize(path),
        "chunk_size": chunk_size,
    }


class SnapshotReader:
    """Random-access reader over a chunked snapshot file."""

    def __init__(self, path: Union[str, os.PathLike]):
        self.path = os.fspath(path)
        self._fh: Optional[BinaryIO] = None
        self._load()

    def __enter__(self) -> "SnapshotReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # -- loading / structural validation ------------------------------------
    def _load(self) -> None:
        file_size = os.path.getsize(self.path)
        if file_size < HEADER_SIZE + TRAILER_SIZE:
            raise CorruptSnapshotError("file too small to be a snapshot")

        fh = open(self.path, "rb")
        try:
            magic, version, _flags = _HEADER.unpack(fh.read(HEADER_SIZE))
            if magic != MAGIC:
                raise CorruptSnapshotError("bad magic")
            if version != VERSION:
                raise CorruptSnapshotError("unsupported version %d" % version)

            fh.seek(-TRAILER_SIZE, os.SEEK_END)
            index_offset, index_length, index_digest = _TRAILER.unpack(
                fh.read(TRAILER_SIZE))
            if index_offset < HEADER_SIZE:
                raise CorruptSnapshotError("index offset before data region")
            if index_offset + index_length + TRAILER_SIZE != file_size:
                raise CorruptSnapshotError("index/trailer do not match file size")

            fh.seek(index_offset)
            raw_index = fh.read(index_length)
            if hashlib.sha256(raw_index).digest() != index_digest:
                raise CorruptSnapshotError("index checksum mismatch")
            try:
                meta = json.loads(raw_index.decode("utf-8"))
                chunk_size = int(meta["chunk_size"])
                records = [ChunkRecord.from_dict(d) for d in meta["chunks"]]
            except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
                raise CorruptSnapshotError("invalid index payload") from exc
        except BaseException:
            fh.close()
            raise

        self._fh = fh
        self.chunk_size = chunk_size
        self.chunks = records
        self.file_size = file_size
        self.index_offset = index_offset
        self.original_size = sum(r.size for r in records)
        self._validate_index()

    def _validate_index(self) -> None:
        expected_offset = HEADER_SIZE
        for i, rec in enumerate(self.chunks):
            if rec.offset != expected_offset:
                raise CorruptSnapshotError(
                    "chunk %d offset %d != expected %d"
                    % (i, rec.offset, expected_offset))
            if rec.compressed_size <= 0 or rec.size <= 0:
                raise CorruptSnapshotError("chunk %d has non-positive size" % i)
            expected_offset += rec.compressed_size
        if expected_offset != self.index_offset:
            raise CorruptSnapshotError("chunks do not end exactly at the index")

    # -- chunked reads -------------------------------------------------------
    def _raw_compressed(self, index: int) -> bytes:
        rec = self.chunks[index]
        assert self._fh is not None
        self._fh.seek(rec.offset)
        payload = self._fh.read(rec.compressed_size)
        if len(payload) != rec.compressed_size:
            raise CorruptChunkError(index, "short compressed read (file truncated)")
        return payload

    def read_chunk(self, index: int, verify: bool = True) -> bytes:
        """Decompress one chunk and (by default) verify its SHA-256."""
        count = len(self.chunks)
        if index < 0 or index >= count:
            raise IndexError("chunk %d out of range (0..%d)" % (index, count - 1))
        rec = self.chunks[index]
        try:
            data = zlib.decompress(self._raw_compressed(index))
        except zlib.error as exc:
            raise CorruptChunkError(index, "zlib decompression failed: %s" % exc) from exc
        if len(data) != rec.size:
            raise CorruptChunkError(index,
                                    "size mismatch: got %d, index says %d"
                                    % (len(data), rec.size))
        if verify and hashlib.sha256(data).hexdigest() != rec.sha256:
            raise CorruptChunkError(index, "sha256 mismatch")
        return data

    def read_range(self, start: int, length: Optional[int] = None,
                   end: Optional[int] = None) -> bytes:
        """Random read of an uncompressed byte range.

        Only chunks overlapping the half-open interval [start, end) are
        decompressed and verified. Bad chunks are never returned.
        """
        if start < 0:
            raise ValueError("start must be >= 0")
        if length is not None and end is not None:
            raise ValueError("pass either length or end, not both")
        if end is None:
            end = start + length if length is not None else self.original_size
        if end < start:
            raise ValueError("end precedes start")
        if end > self.original_size:
            raise ValueError("range [%d,%d) exceeds snapshot size %d"
                             % (start, end, self.original_size))
        if not self.chunks or end == start:
            return b""

        size = self.chunk_size
        pieces: List[bytes] = []
        for ci in range(start // size, (end - 1) // size + 1):
            block = self.read_chunk(ci)
            base = ci * size
            pieces.append(block[max(start - base, 0):
                                min(end - base, len(block))])
        return b"".join(pieces)

    def read_all(self) -> bytes:
        """Decompress and verify every chunk."""
        return b"".join(self.read_chunk(i) for i in range(len(self.chunks)))

    def verify(self) -> List[int]:
        """Verify all chunks; return indices of corrupt chunks ([] = healthy)."""
        bad: List[int] = []
        for i in range(len(self.chunks)):
            try:
                self.read_chunk(i)
            except CorruptChunkError:
                bad.append(i)
        return bad

    def locate(self, offset: int) -> int:
        """Return the chunk index storing uncompressed byte ``offset``."""
        if offset >= self.original_size:
            raise IndexError("offset %d beyond end" % offset)
        return offset // self.chunk_size


def restore_chunks(
    target_path: Union[str, os.PathLike],
    replica_paths: Sequence[Union[str, os.PathLike]],
    indices: Optional[Iterable[int]] = None,
    auto_repair: bool = False,
) -> List[int]:
    """Restore corrupt chunks in the target snapshot from replica copies.

    Replica snapshots must have been written from the identical original
    snapshot: chunk layout, sizes and per-chunk SHA-256 must match. Chunk
    bytes are copied as opaque (still-compressed) data, so a chunk is
    repairable even when the target's own compressed bytes are damaged.

    ``indices`` selects chunks to repair; by default every chunk that fails
    verification is repaired. If ``auto_repair`` is true the fixed container
    is atomically replaced on disk, otherwise it is left untouched.

    Returns the list of repaired chunk indices.
    Raises MissingReplicaChunkError if no healthy replica has a needed chunk.
    """
    target = SnapshotReader(target_path)
    try:
        replicas: List[SnapshotReader] = []
        try:
            for rp in replica_paths:
                replicas.append(SnapshotReader(rp))

            if indices is None:
                wanted = target.verify()
            else:
                wanted = sorted(set(indices))

            repaired: List[int] = []
            replacements = {}
            for ci in wanted:
                if ci < 0 or ci >= len(target.chunks):
                    raise IndexError("chunk %d out of range" % ci)
                rec = target.chunks[ci]
                source_payload = None
                source_path = None
                for rep in replicas:
                    if len(rep.chunks) != len(target.chunks):
                        continue
                    rrec = rep.chunks[ci]
                    if rrec.size != rec.size or rrec.sha256 != rec.sha256:
                        continue
                    payload = rep._raw_compressed(ci)
                    try:
                        data = zlib.decompress(payload)
                    except zlib.error:
                        continue
                    if (len(data) == rec.size
                            and hashlib.sha256(data).hexdigest() == rec.sha256):
                        source_payload = payload
                        source_path = rep.path
                        break
                if source_payload is None:
                    raise MissingReplicaChunkError(
                        "no healthy replica contains chunk %d" % ci)
                replacements[ci] = source_payload
                repaired.append(ci)

            if auto_repair and wanted:
                _rewrite_with_replacements(target, replacements)
        finally:
            for rep in replicas:
                rep.close()
    finally:
        target.close()

    if auto_repair and wanted:
        # Reader is stale after on-disk rewrite; callers reopen as needed.
        pass
    return repaired


def _rewrite_with_replacements(reader: SnapshotReader,
                               replacements: dict) -> None:
    """Rebuild the container, swapping in repaired compressed chunk bytes.

    Offsets are recomputed, so replica chunks may use different compression
    levels (and therefore different compressed sizes).
    """
    assert reader._fh is not None
    records: List[ChunkRecord] = []
    offset = HEADER_SIZE
    payloads: List[bytes] = []
    for i, rec in enumerate(reader.chunks):
        if i in replacements:
            payload = replacements[i]
        else:
            reader._fh.seek(rec.offset)
            payload = reader._fh.read(rec.compressed_size)
            if len(payload) != rec.compressed_size:
                raise CorruptChunkError(i, "short read during rebuild")
        records.append(ChunkRecord(offset, len(payload), rec.size, rec.sha256))
        payloads.append(payload)
        offset += len(payload)

    index_bytes = json.dumps(
        {"chunk_size": reader.chunk_size,
         "chunks": [r.to_dict() for r in records]},
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")

    tmp_path = reader.path + ".repair"
    with open(tmp_path, "wb") as out:
        out.write(_HEADER.pack(MAGIC, VERSION, 0))
        for payload in payloads:
            out.write(payload)
        out.write(index_bytes)
        out.write(_TRAILER.pack(HEADER_SIZE + sum(len(p) for p in payloads),
                                len(index_bytes),
                                hashlib.sha256(index_bytes).digest()))
    os.replace(tmp_path, reader.path)
