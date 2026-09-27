"""Read-only, page-granular access to a paged index file.

Only the pages touched by a query are ever read from disk (via
``os.pread``) and parsed.  A small bounded LRU cache keeps at most
``cache_pages`` pages in memory, so resident memory stays constant no
matter how large the index file is.
"""

import os
import zlib
from collections import OrderedDict

from . import format as fmt


class PagedIndexError(Exception):
    """Base error; ``offset`` locates the problem in the file."""

    def __init__(self, message, offset=None, page_no=None):
        self.offset = offset
        self.page_no = page_no
        loc = []
        if page_no is not None:
            loc.append(f"page={page_no}")
        if offset is not None:
            loc.append(f"offset=0x{offset:x}")
        if loc:
            message = f"{message} ({', '.join(loc)})"
        super().__init__(message)


class CorruptHeaderError(PagedIndexError):
    """File header (page 0) is invalid."""


class TruncatedIndexError(PagedIndexError):
    """File is shorter than the index metadata requires."""


class CorruptPageError(PagedIndexError):
    """A data page failed validation (magic/number/count/checksum)."""


class PagedIndexReader:
    def __init__(self, path, cache_pages=128):
        self.path = path
        self._fd = os.open(path, os.O_RDONLY)
        try:
            self._file_size = os.fstat(self._fd).st_size
            self._read_header()
        except Exception:
            os.close(self._fd)
            raise
        self._cap = fmt.capacity(self.page_size, self.record_size)
        self._num_pages = -(-self.num_records // self._cap)  # ceil
        expected = (1 + self._num_pages) * self.page_size
        if self._file_size < expected:
            raise TruncatedIndexError(
                f"file truncated: header declares {self.num_records} records "
                f"requiring {expected} bytes, file has {self._file_size}",
                offset=self._file_size,
            )
        self._cache_pages = max(1, int(cache_pages))
        self._cache = OrderedDict()
        self.hits = 0
        self.misses = 0

    # -- header ---------------------------------------------------------

    def _read_header(self):
        if self._file_size < fmt.MIN_PAGE_SIZE:
            raise TruncatedIndexError(
                f"file too small for a header page: {self._file_size} bytes",
                offset=0,
            )
        raw = os.pread(self._fd, fmt.FILE_HEADER_USED, 0)
        if len(raw) < fmt.FILE_HEADER_USED:
            raise TruncatedIndexError(
                f"short read on file header: wanted {fmt.FILE_HEADER_USED}, "
                f"got {len(raw)}",
                offset=0,
            )
        magic, version, page_size, record_size, num_records, crc = (
            fmt.FILE_HEADER_STRUCT.unpack(raw)
        )
        if magic != fmt.FILE_MAGIC:
            raise CorruptHeaderError(f"bad file magic {magic!r}", offset=0)
        if version != fmt.FORMAT_VERSION:
            raise CorruptHeaderError(f"unsupported version {version}", offset=8)
        want_crc = zlib.crc32(raw[: fmt.FILE_HEADER_CRC_SPAN])
        if crc != want_crc:
            raise CorruptHeaderError(
                f"header checksum mismatch: stored 0x{crc:08x}, "
                f"computed 0x{want_crc:08x}",
                offset=28,
            )
        try:
            fmt.validate_geometry(page_size, record_size)
        except ValueError as exc:
            raise CorruptHeaderError(f"bad geometry: {exc}", offset=12) from exc
        if self._file_size < page_size:
            raise TruncatedIndexError(
                f"file smaller than one header page: {self._file_size} < "
                f"{page_size}",
                offset=0,
            )
        self.page_size = page_size
        self.record_size = record_size
        self.num_records = num_records

    # -- page access ----------------------------------------------------

    def _load_page(self, page_no):
        offset = (page_no + 1) * self.page_size
        data = os.pread(self._fd, self.page_size, offset)
        if len(data) < self.page_size:
            raise TruncatedIndexError(
                f"short page read: wanted {self.page_size} bytes, "
                f"got {len(data)}",
                offset=offset,
                page_no=page_no,
            )
        magic, stored_no, record_count, crc = fmt.PAGE_HEADER_STRUCT.unpack_from(
            data, 0
        )
        if magic != fmt.PAGE_MAGIC:
            raise CorruptPageError(
                f"bad page magic {magic!r}", offset=offset, page_no=page_no
            )
        if stored_no != page_no:
            raise CorruptPageError(
                f"page number mismatch: header says {stored_no}",
                offset=offset + 4,
                page_no=page_no,
            )
        expected_count = min(
            self._cap, self.num_records - page_no * self._cap
        )
        if record_count != expected_count:
            raise CorruptPageError(
                f"record_count {record_count} != expected {expected_count}",
                offset=offset + 8,
                page_no=page_no,
            )
        want_crc = zlib.crc32(data[fmt.PAGE_HEADER_SIZE:])
        if crc != want_crc:
            raise CorruptPageError(
                f"page checksum mismatch: stored 0x{crc:08x}, "
                f"computed 0x{want_crc:08x}",
                offset=offset + 12,
                page_no=page_no,
            )
        return data

    def _page(self, page_no):
        cached = self._cache.get(page_no)
        if cached is not None:
            self.hits += 1
            self._cache.move_to_end(page_no)
            return cached
        self.misses += 1
        data = self._load_page(page_no)
        self._cache[page_no] = data
        if len(self._cache) > self._cache_pages:
            self._cache.popitem(last=False)
        return data

    # -- public API -----------------------------------------------------

    def __len__(self):
        return self.num_records

    def record_at(self, index):
        """Return ``(key, value_bytes)`` for the record at global ``index``."""
        if not 0 <= index < self.num_records:
            raise IndexError(
                f"record index {index} out of range [0, {self.num_records})"
            )
        page_no, slot = divmod(index, self._cap)
        page = self._page(page_no)
        off = fmt.PAGE_HEADER_SIZE + slot * self.record_size
        key = fmt.KEY_STRUCT.unpack_from(page, off)[0]
        value = bytes(page[off + 8 : off + self.record_size])
        return key, value

    def lookup(self, key):
        """Binary-search ``key``; return value bytes or ``None`` if absent."""
        lo, hi = 0, self.num_records
        while lo < hi:
            mid = (lo + hi) // 2
            page_no, slot = divmod(mid, self._cap)
            page = self._page(page_no)
            off = fmt.PAGE_HEADER_SIZE + slot * self.record_size
            mid_key = fmt.KEY_STRUCT.unpack_from(page, off)[0]
            if mid_key == key:
                return bytes(page[off + 8 : off + self.record_size])
            if mid_key < key:
                lo = mid + 1
            else:
                hi = mid
        return None

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
