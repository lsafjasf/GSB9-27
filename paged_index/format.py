"""On-disk layout of the paged index.

File layout
-----------
The file is a sequence of fixed-size pages of ``page_size`` bytes:

    +-------------------+----------------------------+-----+----------------------------+
    | page 0: file hdr  | page 1: data page 0        | ... | page N: data page N-1      |
    +-------------------+----------------------------+-----+----------------------------+

Because the file header occupies exactly one page (page 0), every data
page ``k`` (0-based) starts at an absolute file offset of ``(k + 1) *
page_size``, i.e. all page boundaries are aligned to multiples of
``page_size``.  ``page_size`` itself must be a multiple of 64 so pages
also stay aligned to cache-line / sector friendly boundaries.

File header (page 0), little-endian, 32 bytes used, rest zero padded:

    offset  size  field
    0       8     magic = b"PGIDX001"
    8       4     version (currently 1)
    12      4     page_size in bytes
    16      4     record_size in bytes (>= 8)
    20      8     num_records (uint64)
    28      4     crc32 of header bytes [0:28]
    32..    zero padding up to page_size

Data page (page_size bytes):

    offset  size  field
    0       4     page magic = b"PGHD"
    4       4     page number (0-based among data pages)
    8       4     record_count actually stored in this page
    12      4     crc32 of the payload area [32:page_size]
    16      16    reserved, zero
    32..    records, then zero padding

Records are fixed size and never cross a page boundary.  Each page holds

    capacity = (page_size - PAGE_HEADER_SIZE) // record_size

records; when ``record_size`` does not divide the payload area the
remaining ``page_size - PAGE_HEADER_SIZE - capacity * record_size``
bytes are zero padding that is still covered by the page CRC.

Record layout: 8-byte little-endian uint64 key followed by
``record_size - 8`` bytes of opaque value.  Records are sorted by key
across the whole file so lookups are a binary search that only touches
O(log n) pages.

Offset computation for the i-th record (0-based, global):

    page_no = i // capacity
    slot    = i %  capacity
    offset  = (page_no + 1) * page_size + PAGE_HEADER_SIZE + slot * record_size
"""

import struct

FILE_MAGIC = b"PGIDX001"
PAGE_MAGIC = b"PGHD"
FORMAT_VERSION = 1

PAGE_HEADER_SIZE = 32
PAGE_HEADER_STRUCT = struct.Struct("<4sIII")  # magic, page_no, record_count, crc32
assert PAGE_HEADER_STRUCT.size == 16  # remaining 16 bytes are reserved zeros

FILE_HEADER_STRUCT = struct.Struct("<8sIIIQI")  # magic, version, page_size,
# record_size, num_records, crc32
FILE_HEADER_USED = FILE_HEADER_STRUCT.size  # 32; crc covers bytes [0:28]
FILE_HEADER_CRC_SPAN = FILE_HEADER_USED - 4

MIN_PAGE_SIZE = 128
PAGE_SIZE_ALIGNMENT = 64

KEY_STRUCT = struct.Struct("<Q")


def capacity(page_size, record_size):
    """Number of records that fit in one data page."""
    return (page_size - PAGE_HEADER_SIZE) // record_size


def validate_geometry(page_size, record_size):
    if page_size < MIN_PAGE_SIZE:
        raise ValueError(f"page_size {page_size} < minimum {MIN_PAGE_SIZE}")
    if page_size % PAGE_SIZE_ALIGNMENT != 0:
        raise ValueError(
            f"page_size {page_size} must be a multiple of {PAGE_SIZE_ALIGNMENT}"
        )
    if record_size < KEY_STRUCT.size:
        raise ValueError(f"record_size {record_size} must be >= {KEY_STRUCT.size}")
    if capacity(page_size, record_size) < 1:
        raise ValueError(
            f"page_size {page_size} too small for record_size {record_size}"
        )


def record_offset(index, page_size, record_size):
    """Absolute file offset of the record with global 0-based ``index``."""
    cap = capacity(page_size, record_size)
    page_no, slot = divmod(index, cap)
    return (page_no + 1) * page_size + PAGE_HEADER_SIZE + slot * record_size
