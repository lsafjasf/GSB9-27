"""Read-only paged index access (standard library only)."""

from .builder import build_index
from .reader import (
    CorruptHeaderError,
    CorruptPageError,
    PagedIndexError,
    PagedIndexReader,
    TruncatedIndexError,
)

__all__ = [
    "build_index",
    "PagedIndexReader",
    "PagedIndexError",
    "CorruptHeaderError",
    "CorruptPageError",
    "TruncatedIndexError",
]
