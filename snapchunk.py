"""snapchunk: chunked, indexed, integrity-checked snapshot compression.

A snapshot (raw bytes) is split into fixed-size blocks.  Each block is
compressed independently with zlib (standard library) and SHA-256 digests of
the *decompressed* block are stored in a trailing block index, together with
the block's compressed offset/size.  The index is checksummed in a footer,
which makes random access cheap (one index read + one block read) and lets a
corrupt block be pinpointed by its index instead of silently returned.

File layout (all integers little-endian)::

    [magic "SNPK"][version u8][flags u8]          file header (10 bytes)
    [block header][compressed payload] * n       data region
    [index entry: orig_len u32, comp_len u32,
     offset u64, digest 32B] * n                  index region
    [index_length u64][orig_size u64][block_size u64]
    [index_digest 32B][magic "SNPK"]              footer (58 bytes)

Only the standard library is required (Python >= 3.8).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
import zlib
from dataclasses import dataclass
from typing import BinaryIO, Iterable, List, NamedTuple, Optional, Tuple

__all__ = [
    "MAGIC",
    "VERSION",
    "IndexEntry",
    "SnapshotInfo",
    "VerifyReport",
    "CorruptionError",
    "BlockCorruptionError",
    "IndexCorruptionError",
    "FormatError",
    "compress_snapshot",
    "Archive",
    "open_archive",
    "read_range",
    "extract_snapshot",
    "verify_archive",
    "restore_block",
    "recover_archive",
]

MAGIC = b"SNPK"
VERSION = 1
_DEFAULT_LEVEL = 6

# struct formats
_F_HEADER = struct.Struct("<4sBB")                 # magic, version, flags
_F_BLOCK = struct.Struct("<II")                    # orig_len, comp_len
_F_ENTRY = struct.Struct("<IIQ")                   # orig_len, comp_len, offset
_F_FOOTER = struct.Struct("<QQQ")                  # idx_len, orig_size, block
_FOOTER_SIZE = _F_FOOTER.size + 32 + len(MAGIC)    # + index_digest + magic
HEADER_SIZE = _F_HEADER.size
ENTRY_SIZE = _F_ENTRY.size + hashlib.sha256().digest_size


# ---------------------------------------------------------------- errors --


class CorruptionError(Exception):
    """Base class: the archive contains damaged data."""


class FormatError(CorruptionError):
    """The file is not a valid snapchunk archive (bad magic/footer)."""


class IndexCorruptionError(CorruptionError):
    """The block index itself is damaged or unreadable."""


class BlockCorruptionError(CorruptionError):
    """A specific block failed its checksum or could not be decompressed.

    Attributes:
        block_index: zero-based index of the bad block.
        expected:    expected SHA-256 digest (hex) from the block index.
        actual:      observed SHA-256 digest (hex), or None if decompression
                     itself failed.
    """

    def __init__(
        self,
        block_index: int,
        expected: Optional[str],
        actual: Optional[str],
        message: str,
    ) -> None:
        super().__init__(message)
        self.block_index = block_index
        self.expected = expected
        self.actual = actual


# ----------------------------------------------------------------- types --


class IndexEntry(NamedTuple):
    block_index: int
    offset: int
    compressed_size: int
    original_size: int
    digest: bytes

    def digest_hex(self) -> str:
        return self.digest.hex()


@dataclass(frozen=True)
class SnapshotInfo:
    original_size: int
    compressed_size: int
    block_size: int
    num_blocks: int

    def ratio(self) -> float:
        """compressed / original; lower is better (1.0 for empty input)."""
        if self.original_size == 0:
            return 1.0
        return self.compressed_size / self.original_size


@dataclass
class VerifyReport:
    ok: bool
    total_blocks: int
    bad_blocks: List[int]
    errors: List[BlockCorruptionError]


# ------------------------------------------------------------- internals --


def _digest(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _as_bytes(source) -> bytes:
    if isinstance(source, bytes):
        return source
    if isinstance(source, (bytearray, memoryview)):
        return bytes(source)
    data = source.read()
    return data if isinstance(data, bytes) else bytes(data)


def _num_blocks(orig_size: int, block_size: int) -> int:
    return (orig_size + block_size - 1) // block_size if orig_size else 0


def _build_index_blob(entries: Iterable[IndexEntry]) -> bytes:
    return b"".join(
        _F_ENTRY.pack(e.original_size, e.compressed_size, e.offset) + e.digest
        for e in entries
    )


def _parse_index_blob(blob: bytes) -> List[IndexEntry]:
    if len(blob) % ENTRY_SIZE:
        raise IndexCorruptionError(
            "index region has truncated size %d (not a multiple of %d)"
            % (len(blob), ENTRY_SIZE)
        )
    entries = []
    for idx in range(0, len(blob), ENTRY_SIZE):
        orig_len, comp_len, offset = _F_ENTRY.unpack_from(blob, idx)
        digest = blob[idx + _F_ENTRY.size : idx + ENTRY_SIZE]
        entries.append(
            IndexEntry(idx // ENTRY_SIZE, offset, comp_len, orig_len, digest)
        )
    return entries


class _ArchiveWriter:
    """Streams data blocks and appends the index + footer at close."""

    def __init__(self, fh: BinaryIO, block_size: int, level: int) -> None:
        self._fh = fh
        self.block_size = block_size
        self.level = level
        self._entries: List[IndexEntry] = []
        self._orig_size = 0
        fh.write(_F_HEADER.pack(MAGIC, VERSION, 0))

    def write_block(self, block: bytes) -> IndexEntry:
        idx = len(self._entries)
        compressed = zlib.compress(block, self.level)
        offset = self._fh.tell()
        self._fh.write(_F_BLOCK.pack(len(block), len(compressed)))
        self._fh.write(compressed)
        entry = IndexEntry(idx, offset, len(compressed), len(block), _digest(block))
        self._entries.append(entry)
        self._orig_size += len(block)
        return entry

    def finish(self) -> SnapshotInfo:
        index_blob = _build_index_blob(self._entries)
        self._fh.write(index_blob)
        index_offset = self._fh.tell() - len(index_blob)
        footer = _F_FOOTER.pack(
            len(index_blob), self._orig_size, self.block_size
        ) + _digest(index_blob) + MAGIC
        self._fh.write(footer)
        total = self._fh.tell()
        self._fh.flush()
        return SnapshotInfo(
            self._orig_size, total, self.block_size, len(self._entries)
        )


# ------------------------------------------------------------- compress --


def compress_snapshot(
    source,
    dest,
    block_size: int = 64 * 1024,
    level: int = _DEFAULT_LEVEL,
) -> SnapshotInfo:
    """Compress ``source`` (bytes or binary file object) into ``dest`` path.

    Returns a :class:`SnapshotInfo` describing the result.
    """
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    if not 0 <= level <= 9:
        raise ValueError("level must be in 0..9")
    data = _as_bytes(source)
    with open(dest, "wb") as fh:
        writer = _ArchiveWriter(fh, block_size, level)
        for start in range(0, len(data), block_size):
            writer.write_block(data[start : start + block_size])
        return writer.finish()


# ---------------------------------------------------------------- reader --


class Archive:
    """Random-access reader for a snapchunk archive.

    The whole file is never decompressed; only the footer/index (loaded once)
    and the requested blocks are touched.  Use as a context manager::

        with Archive("snap.snpk") as ar:
            chunk = ar.read_range(10_000, 50)
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._fh = open(path, "rb")
        try:
            self._load_meta()
        except Exception:
            self._fh.close()
            raise

    # -- setup -----------------------------------------------------------

    def _load_meta(self) -> None:
        fh = self._fh
        fh.seek(0, os.SEEK_END)
        file_size = fh.tell()
        if file_size < HEADER_SIZE + _FOOTER_SIZE:
            raise FormatError(
                "%s: file too small (%d bytes) to be a snapshot archive"
                % (self.path, file_size)
            )
        fh.seek(0)
        magic, version, flags = _F_HEADER.unpack(fh.read(HEADER_SIZE))
        if magic != MAGIC:
            raise FormatError("%s: bad header magic %r" % (self.path, magic))
        if version != VERSION:
            raise FormatError(
                "%s: unsupported archive version %d" % (self.path, version)
            )
        self.version = version
        self.flags = flags

        fh.seek(-_FOOTER_SIZE, os.SEEK_END)
        footer_tail = fh.read(_FOOTER_SIZE)
        index_length, orig_size, block_size = _F_FOOTER.unpack(
            footer_tail[: _F_FOOTER.size]
        )
        index_digest = footer_tail[_F_FOOTER.size : _F_FOOTER.size + 32]
        tail_magic = footer_tail[_F_FOOTER.size + 32 :]
        if tail_magic != MAGIC:
            raise FormatError("%s: bad footer magic" % self.path)
        if index_length % ENTRY_SIZE:
            raise IndexCorruptionError(
                "footer index length %d is not a multiple of entry size %d"
                % (index_length, ENTRY_SIZE)
            )
        index_offset = file_size - _FOOTER_SIZE - index_length
        if index_offset < HEADER_SIZE:
            raise IndexCorruptionError(
                "index offset %d overlaps the file header" % index_offset
            )
        fh.seek(index_offset)
        index_blob = fh.read(index_length)
        if _digest(index_blob) != index_digest:
            raise IndexCorruptionError(
                "%s: block index checksum mismatch" % self.path
            )
        entries = _parse_index_blob(index_blob)

        if block_size <= 0:
            raise IndexCorruptionError("non-positive block size in footer")
        expected_blocks = _num_blocks(orig_size, block_size)
        if len(entries) != expected_blocks:
            raise IndexCorruptionError(
                "index lists %d blocks but footer size implies %d"
                % (len(entries), expected_blocks)
            )
        total = sum(e.original_size for e in entries)
        if total != orig_size:
            raise IndexCorruptionError(
                "index block sizes sum to %d but footer says %d"
                % (total, orig_size)
            )
        for pos, entry in enumerate(entries):
            end = entry.offset + _F_BLOCK.size + entry.compressed_size
            if entry.block_index != pos or not (
                HEADER_SIZE <= entry.offset and end <= index_offset
            ):
                raise IndexCorruptionError(
                    "entry #%d has out-of-range offset/size" % pos
                )

        self._entries = entries
        self.original_size = orig_size
        self.block_size = block_size
        self.num_blocks = len(entries)
        self._index_offset = index_offset
        self._file_size = file_size
        self._cache = {}

    # -- properties / context --------------------------------------------

    @property
    def entries(self) -> Tuple[IndexEntry, ...]:
        return tuple(self._entries)

    def info(self) -> SnapshotInfo:
        return SnapshotInfo(
            self.original_size,
            self._file_size,
            self.block_size,
            self.num_blocks,
        )

    def __enter__(self) -> "Archive":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self._fh.close()

    # -- reading ---------------------------------------------------------

    def _read_raw_block(self, index: int) -> Tuple[bytes, IndexEntry]:
        entry = self._entries[index]
        self._fh.seek(entry.offset)
        header = self._fh.read(_F_BLOCK.size)
        if len(header) != _F_BLOCK.size:
            raise BlockCorruptionError(
                index, entry.digest_hex(), None,
                "block %d header is truncated" % index,
            )
        stored_orig, stored_comp = _F_BLOCK.unpack(header)
        if stored_orig != entry.original_size or stored_comp != entry.compressed_size:
            raise BlockCorruptionError(
                index, entry.digest_hex(), None,
                "block %d header disagrees with index" % index,
            )
        payload = self._fh.read(entry.compressed_size)
        if len(payload) != entry.compressed_size:
            raise BlockCorruptionError(
                index, entry.digest_hex(), None,
                "block %d payload is truncated" % index,
            )
        return payload, entry

    def read_block(
        self, index: int, *, verify: bool = True, use_cache: bool = True
    ) -> bytes:
        """Return the *decompressed* bytes of block ``index``.

        When ``verify`` is true (default) the decompressed block is compared
        against the index digest; mismatch raises :class:`BlockCorruptionError`
        and no wrong data is returned.
        """
        if use_cache and verify and index in self._cache:
            return self._cache[index]
        if not 0 <= index < self.num_blocks:
            raise IndexError(
                "block index %d out of range (0..%d)"
                % (index, self.num_blocks - 1)
            )
        payload, entry = self._read_raw_block(index)
        try:
            block = zlib.decompress(payload)
        except zlib.error as exc:
            raise BlockCorruptionError(
                index, entry.digest_hex(), None,
                "block %d failed to decompress: %s" % (index, exc),
            ) from exc
        if len(block) != entry.original_size:
            raise BlockCorruptionError(
                index, entry.digest_hex(), _digest(block).hex(),
                "block %d decompressed to %d bytes, index says %d"
                % (index, len(block), entry.original_size),
            )
        actual = _digest(block)
        if actual != entry.digest:
            raise BlockCorruptionError(
                index, entry.digest_hex(), actual.hex(),
                "block %d checksum mismatch" % index,
            )
        if use_cache:
            self._cache[index] = block
        return block

    def read_range(self, start: int, length: Optional[int] = None) -> bytes:
        """Random access: return ``length`` decompressed bytes from ``start``.

        With one argument it behaves like slicing ``data[start:]``; negative
        start indexes from the end.  Only blocks overlapping the range are
        read and decompressed.
        """
        if self.original_size == 0:
            if start not in (0, -0) or (length is not None and length != 0):
                raise ValueError("cannot read a non-empty range of empty snapshot")
            return b""
        if start < 0:
            start += self.original_size
        if not 0 <= start <= self.original_size:
            raise ValueError("start offset %d out of range" % start)
        if length is None:
            end = self.original_size
        else:
            if length < 0:
                raise ValueError("length must be non-negative")
            end = min(self.original_size, start + length)
        if start >= end:
            return b""
        first = start // self.block_size
        last = (end - 1) // self.block_size
        pieces = []
        for block_idx in range(first, last + 1):
            block = self.read_block(block_idx)
            block_start = block_idx * self.block_size
            lo = max(start, block_start) - block_start
            hi = min(end, block_start + len(block)) - block_start
            pieces.append(block[lo:hi])
        return b"".join(pieces)

    def extract_all(self) -> bytes:
        """Decompress every block (verifying each) and concatenate."""
        return b"".join(
            self.read_block(i, use_cache=False) for i in range(self.num_blocks)
        )

    def verify(self) -> VerifyReport:
        """Read + verify every block; never raises for bad block data."""
        bad: List[int] = []
        errors: List[BlockCorruptionError] = []
        for i in range(self.num_blocks):
            try:
                self.read_block(i, use_cache=False)
            except BlockCorruptionError as exc:
                bad.append(i)
                errors.append(exc)
        return VerifyReport(not bad, self.num_blocks, bad, errors)


# --------------------------------------------------------- module-level --


def open_archive(path: str) -> Archive:
    return Archive(path)


def read_range(path: str, start: int, length: Optional[int] = None) -> bytes:
    with Archive(path) as ar:
        return ar.read_range(start, length)


def extract_snapshot(path: str) -> bytes:
    with Archive(path) as ar:
        return ar.extract_all()


def verify_archive(path: str) -> VerifyReport:
    with Archive(path) as ar:
        return ar.verify()


def restore_block(
    primary_path: str, block_index: int, replica_paths: Iterable[str]
) -> int:
    """Rebuild one corrupt block of ``primary_path`` from good replicas.

    The repaired primary is rewritten atomically.  Raises
    :class:`BlockCorruptionError` (naming ``block_index``) if every copy of
    that block is bad.  Returns 1 on success.
    """
    recover_archive(primary_path, replica_paths)
    with Archive(primary_path) as ar:
        ar.read_block(block_index)
    return 1


# --------------------------------------------------------------- recover --


def _primary_healthy(primary: Archive, index: int) -> bool:
    try:
        primary.read_block(index, use_cache=False)
    except BlockCorruptionError:
        return False
    return True


def _recover_archive(
    primary_path: str, replica_paths: List[str]
) -> Tuple[str, int, int]:
    """Internal: return (dest_path, num_blocks, repaired_count).

    A block counts as repaired when the primary cannot serve a byte-identical
    copy (bad data, bad header, or unreadable index).
    """

    # Open the primary if possible; otherwise fall back to a replica whose
    # index/footer is intact, so a corrupted primary index is recoverable.
    primary: Optional[Archive] = None
    try:
        primary = Archive(primary_path)
    except CorruptionError:
        primary = None

    good_source: Optional[Archive] = primary
    replicas: List[Archive] = []
    try:
        for path in replica_paths:
            try:
                ar = Archive(path)
            except CorruptionError:
                continue
            replicas.append(ar)
            if good_source is None:
                good_source = ar

        if good_source is None:
            raise IndexCorruptionError(
                "no readable index among primary and replicas; cannot recover"
            )

        block_size = good_source.block_size
        orig_size = good_source.original_size
        for ar in replicas:
            if ar.original_size != orig_size or ar.block_size != block_size:
                raise ValueError(
                    "replica %s describes a different snapshot "
                    "(original_size/block_size mismatch)" % ar.path
                )

        all_sources = [good_source] + [
            ar for ar in replicas if ar is not good_source
        ]

        def healthy_block(index: int) -> Optional[bytes]:
            for ar in all_sources:
                if index >= ar.num_blocks:
                    continue
                try:
                    return ar.read_block(index, use_cache=False)
                except BlockCorruptionError:
                    continue
            return None

        tmp_path = primary_path + ".recover.tmp"
        repaired = 0
        with open(tmp_path, "wb") as out:
            writer = _ArchiveWriter(out, block_size, _DEFAULT_LEVEL)
            for index in range(good_source.num_blocks):
                block = healthy_block(index)
                if block is None:
                    raise BlockCorruptionError(
                        index, None, None,
                        "block %d is corrupt in primary and every replica"
                        % index,
                    )
                primary_ok = (
                    primary is not None
                    and _digest(block) == primary._entries[index].digest
                    and _primary_healthy(primary, index)
                )
                if not primary_ok:
                    repaired += 1
                writer.write_block(block)
            info = writer.finish()
        os.replace(tmp_path, primary_path)
        return primary_path, info.num_blocks, repaired
    finally:
        if primary is not None:
            primary.close()
        for ar in replicas:
            ar.close()


def recover_archive(
    primary_path: str,
    replica_paths: Iterable[str]
) -> int:
    """Repair ``primary_path`` block by block from healthy replicas.

    Every block must exist uncorrupted in at least one of primary/replicas.
    The repaired file is atomically renamed over the primary.  Returns the
    number of blocks that were rebuilt.  Raises :class:`BlockCorruptionError`
    naming the block index if no healthy copy exists for a block.
    """
    _dest, _total, repaired = _recover_archive(
        primary_path, list(replica_paths)
    )
    return repaired


# ------------------------------------------------------------------- CLI --


def _cli(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="snapchunk",
        description="Chunked, indexed, checksummed snapshot compression.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_c = sub.add_parser("compress", help="compress a snapshot file")
    p_c.add_argument("source")
    p_c.add_argument("dest")
    p_c.add_argument("-b", "--block-size", type=int, default=64 * 1024)
    p_c.add_argument("-l", "--level", type=int, default=_DEFAULT_LEVEL)

    p_x = sub.add_parser("extract", help="decompress the whole snapshot")
    p_x.add_argument("archive")
    p_x.add_argument("dest", nargs="?", help="output file (default stdout)")

    p_r = sub.add_parser("range", help="read a decompressed byte range")
    p_r.add_argument("archive")
    p_r.add_argument("start", type=int)
    p_r.add_argument("length", type=int)
    p_r.add_argument("dest", nargs="?")

    p_b = sub.add_parser("read-block", help="decompress one block")
    p_b.add_argument("archive")
    p_b.add_argument("index", type=int)

    sub.add_parser("verify", help="verify every block checksum").add_argument(
        "archive"
    )
    p_info = sub.add_parser("info", help="show index summary")
    p_info.add_argument("archive")

    p_rec = sub.add_parser("recover", help="repair archive from replicas")
    p_rec.add_argument("archive")
    p_rec.add_argument("replicas", nargs="+")

    args = parser.parse_args(argv)

    def fail(exc: CorruptionError, code: int = 2) -> int:
        if isinstance(exc, BlockCorruptionError):
            detail = "block %d corrupt" % exc.block_index
            if exc.actual is not None:
                detail += " (sha256 mismatch)"
            elif "decompress" in str(exc):
                detail += " (decompression failed)"
            else:
                detail += " (bad/truncated block header)"
        else:
            detail = str(exc)
        print("snapchunk: %s: %s" % (type(exc).__name__, detail),
              file=sys.stderr)
        return code

    try:
        return _run(args, parser)
    except (CorruptionError, ValueError, IndexError) as exc:
        return fail(exc) if isinstance(exc, CorruptionError) else _usage_fail(
            parser, exc)


def _usage_fail(parser, exc) -> int:
    parser.error(str(exc))
    return 1


def _run(args, parser) -> int:
    if args.cmd == "compress":
        with open(args.source, "rb") as fh:
            info = compress_snapshot(fh, args.dest, args.block_size, args.level)
        print(
            "blocks=%d original=%d compressed=%d ratio=%.4f"
            % (info.num_blocks, info.original_size,
               info.compressed_size, info.ratio())
        )
        return 0

    if args.cmd == "info":
        with Archive(args.archive) as ar:
            info = ar.info()
            print("original_size:", info.original_size)
            print("compressed_size:", info.compressed_size)
            print("block_size:", info.block_size)
            print("num_blocks:", info.num_blocks)
            print("ratio: %.4f" % info.ratio())
            for e in ar.entries[:5]:
                print(
                    "  block %d offset=%d comp=%d orig=%d sha256=%s..."
                    % (e.block_index, e.offset, e.compressed_size,
                       e.original_size, e.digest_hex()[:16])
                )
            if info.num_blocks > 5:
                print("  ... (%d more)" % (info.num_blocks - 5))
        return 0

    if args.cmd == "extract":
        data = extract_snapshot(args.archive)
        if args.dest:
            with open(args.dest, "wb") as fh:
                fh.write(data)
        else:
            sys.stdout.buffer.write(data)
        return 0

    if args.cmd == "range":
        data = read_range(args.archive, args.start, args.length)
        if args.dest:
            with open(args.dest, "wb") as fh:
                fh.write(data)
        else:
            sys.stdout.buffer.write(data)
        return 0

    if args.cmd == "read-block":
        with Archive(args.archive) as ar:
            sys.stdout.buffer.write(ar.read_block(args.index))
        return 0

    if args.cmd == "verify":
        report = verify_archive(args.archive)
        if report.ok:
            print("OK: %d blocks verified" % report.total_blocks)
            return 0
        print("CORRUPT blocks: %s" % report.bad_blocks, file=sys.stderr)
        return 2

    if args.cmd == "recover":
        n = recover_archive(args.archive, args.replicas)
        print("repaired; %d blocks rebuilt" % n)
        return 0

    parser.error("unknown command")
    return 1  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(_cli())
