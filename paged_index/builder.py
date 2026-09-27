"""Streaming builder for paged index files (test/benchmark data generator)."""

import os
import zlib

from . import format as fmt


def _pack_value(value, value_len):
    if isinstance(value, int):
        return value.to_bytes(value_len, "little")
    value = bytes(value)
    if len(value) != value_len:
        raise ValueError(f"value must be {value_len} bytes, got {len(value)}")
    return value


def build_index(path, records, page_size=4096, record_size=16):
    """Write a paged index to ``path``.

    ``records`` is an iterable of ``(key, value)`` pairs sorted by
    ascending key.  ``value`` is an int or a bytes-like of exactly
    ``record_size - 8`` bytes.  Returns the number of records written.
    """
    fmt.validate_geometry(page_size, record_size)
    cap = fmt.capacity(page_size, record_size)
    value_len = record_size - fmt.KEY_STRUCT.size

    count = 0
    prev_key = None
    page = bytearray(page_size)
    slot = 0
    page_no = 0

    with open(path, "w+b") as fh:
        fh.write(bytes(page_size))  # placeholder for the header page

        def flush_page():
            payload = bytes(page[fmt.PAGE_HEADER_SIZE:])
            crc = zlib.crc32(payload)
            fmt.PAGE_HEADER_STRUCT.pack_into(
                page, 0, fmt.PAGE_MAGIC, page_no, slot, crc
            )
            fh.write(page)

        for key, value in records:
            key = int(key)
            if key < 0 or key >= 1 << 64:
                raise ValueError(f"key out of uint64 range: {key}")
            if prev_key is not None and key < prev_key:
                raise ValueError("records must be sorted by ascending key")
            prev_key = key
            off = fmt.PAGE_HEADER_SIZE + slot * record_size
            fmt.KEY_STRUCT.pack_into(page, off, key)
            page[off + 8 : off + record_size] = _pack_value(value, value_len)
            slot += 1
            count += 1
            if slot == cap:
                flush_page()
                page[:] = b"\x00" * page_size
                slot = 0
                page_no += 1
        if slot:
            flush_page()

        header = fmt.FILE_HEADER_STRUCT.pack(
            fmt.FILE_MAGIC, fmt.FORMAT_VERSION, page_size, record_size, count, 0
        )
        crc = zlib.crc32(header[: fmt.FILE_HEADER_CRC_SPAN])
        header = fmt.FILE_HEADER_STRUCT.pack(
            fmt.FILE_MAGIC, fmt.FORMAT_VERSION, page_size, record_size, count, crc
        )
        fh.seek(0)
        fh.write(header)
        fh.flush()
        os.fsync(fh.fileno())
    return count
