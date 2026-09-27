"""只读分页索引库（纯标准库）。

文件布局（所有页按 page_size 对齐，页 i 的文件偏移 = i * page_size）：

  页 0：文件头页
    [0:8]   magic = b"PGIDX001"
    [8:12]  version = 1 (u32 BE)
    [12:16] page_size (u32 BE)
    [16:20] record_size (u32 BE)
    [20:24] 保留 (u32)
    [24:32] num_pages (u64 BE，含头页)
    [32:40] num_records (u64 BE)
    [40:44] header_crc = crc32(header[0:40]) (u32 BE)
    [44:page_size] 零填充

  页 1..N：数据页
    [0:4]   page_magic = 0x50474944 ("PGID")
    [4:8]   page_no (u32 BE，自校验)
    [8:12]  num_records_in_page (u32 BE)
    [12:16] payload_crc = crc32(records 区) (u32 BE)
    [16:16+num*record_size] 记录区，每条记录 = key(u64 BE) + payload
    剩余为填充：当 (page_size - 16) % record_size != 0 时尾部填充零字节，
    每页记录数 records_per_page = (page_size - 16) // record_size（向下取整）。

查询：记录按 key 全局升序，逻辑记录 i 位于
  page_no = 1 + i // records_per_page, slot = i % records_per_page
二分查找只 pread 命中的页；页缓存为有界 LRU，内存占用与文件大小无关。
"""

from __future__ import annotations

import os
import struct
import zlib
from collections import OrderedDict

FILE_MAGIC = b"PGIDX001"
VERSION = 1
PAGE_MAGIC = 0x50474944  # "PGID"
HEADER_SIZE = 44
PAGE_HEADER_SIZE = 16
KEY_SIZE = 8

_HEADER_STRUCT = struct.Struct(">8sIIIIQQI")  # 44 bytes
_PAGE_HEADER_STRUCT = struct.Struct(">IIII")  # 16 bytes
_KEY_STRUCT = struct.Struct(">Q")


class IndexError_(Exception):
    """本库所有错误的基类。"""


class FormatError(IndexError_):
    """文件头非法（魔数/版本/参数/CRC 不符）。"""


class TruncatedError(IndexError_):
    """文件被截断：实际大小小于头部声明的大小。"""

    def __init__(self, expected: int, actual: int, page_size: int = 0):
        self.expected = expected
        self.actual = actual
        if page_size:
            self.first_bad_page = actual // page_size
            loc = (f", first incomplete page {self.first_bad_page} "
                   f"at offset {self.first_bad_page * page_size}")
        else:
            self.first_bad_page = None
            loc = ""
        super().__init__(
            f"index truncated: expected {expected} bytes, got {actual}{loc}"
        )


class CorruptPageError(IndexError_):
    """数据页校验失败，携带页号与文件偏移便于定位。"""

    def __init__(self, page_no: int, offset: int, reason: str):
        self.page_no = page_no
        self.offset = offset
        self.reason = reason
        super().__init__(
            f"corrupt page {page_no} at file offset {offset}: {reason}"
        )


def records_per_page(page_size: int, record_size: int) -> int:
    return (page_size - PAGE_HEADER_SIZE) // record_size


def build_index(path, records, page_size: int = 4096, record_size: int = 16,
                presorted: bool = False) -> dict:
    """把 (key:int, payload:bytes) 序列写成索引文件，返回统计信息。

    records 可无序、可为任意可迭代对象；payload 长度须 <= record_size - 8，
    不足补零。record_size 不要求整除 page_size，页尾自动填充对齐。
    presorted=True 时要求输入已按 key 升序，构建过程流式进行，
    内存占用 O(page_size)，可用于构建远超内存的索引文件。
    """
    if page_size < 128 or page_size & (page_size - 1) != 0:
        raise ValueError("page_size must be a power of two >= 128")
    if record_size < KEY_SIZE + 1:
        raise ValueError("record_size must be >= 9 (8-byte key + payload)")
    rpp = records_per_page(page_size, record_size)
    if rpp < 1:
        raise ValueError("record_size too large for page_size")

    if not presorted:
        records = sorted((int(k), bytes(v)) for k, v in records)

    def _header(num_pages: int, num_records: int) -> bytes:
        crc = 0
        h = _HEADER_STRUCT.pack(FILE_MAGIC, VERSION, page_size, record_size,
                                0, num_pages, num_records, crc)
        crc = zlib.crc32(h[:40]) & 0xFFFFFFFF
        h = _HEADER_STRUCT.pack(FILE_MAGIC, VERSION, page_size, record_size,
                                0, num_pages, num_records, crc)
        return h + b"\x00" * (page_size - len(h))

    tmp_path = path + ".tmp"
    num_records = 0
    page_no = 0  # 最后写出的数据页号
    with open(tmp_path, "w+b") as f:
        f.write(b"\x00" * page_size)  # 头页占位，收尾时回填
        buf = []
        prev_key = -1

        def flush():
            nonlocal page_no
            page_no += 1
            payload = b"".join(buf)
            phdr = _PAGE_HEADER_STRUCT.pack(
                PAGE_MAGIC, page_no, len(buf),
                zlib.crc32(payload) & 0xFFFFFFFF,
            )
            page = phdr + payload
            page += b"\x00" * (page_size - len(page))  # 页尾填充对齐
            f.write(page)
            buf.clear()

        for k, v in records:
            k = int(k)
            v = bytes(v)
            if k < 0 or k >= 1 << 64:
                raise ValueError(f"key out of u64 range: {k}")
            if len(v) > record_size - KEY_SIZE:
                raise ValueError(f"payload too long for record_size={record_size}")
            if presorted and k < prev_key:
                raise ValueError("presorted input is not sorted")
            prev_key = k
            buf.append(_KEY_STRUCT.pack(k) + v
                       + b"\x00" * (record_size - KEY_SIZE - len(v)))
            num_records += 1
            if len(buf) == rpp:
                flush()
        if buf:
            flush()
        num_pages = 1 + page_no
        f.seek(0)
        f.write(_header(num_pages, num_records))
    os.replace(tmp_path, path)
    return {
        "num_records": num_records,
        "num_pages": num_pages,
        "page_size": page_size,
        "record_size": record_size,
        "file_size": num_pages * page_size,
    }


class PagedIndexReader:
    """只读索引查询器。只解析查询涉及的页，内存占用有界。"""

    def __init__(self, path, cache_pages: int = 16):
        self.path = path
        self._fd = os.open(path, os.O_RDONLY)
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._cache_cap = max(1, cache_pages)
        self.pages_read = 0  # 统计：实际从磁盘读取的页数

        actual_size = os.fstat(self._fd).st_size
        if actual_size < HEADER_SIZE:
            raise TruncatedError(HEADER_SIZE, actual_size)
        raw = os.pread(self._fd, HEADER_SIZE, 0)
        if len(raw) < HEADER_SIZE:
            raise TruncatedError(HEADER_SIZE, len(raw))
        (magic, version, page_size, record_size, _reserved,
         num_pages, num_records, header_crc) = _HEADER_STRUCT.unpack(raw)
        if magic != FILE_MAGIC:
            raise FormatError(f"bad file magic: {magic!r}")
        if version != VERSION:
            raise FormatError(f"unsupported version: {version}")
        if zlib.crc32(raw[:40]) & 0xFFFFFFFF != header_crc:
            raise FormatError("header CRC mismatch")
        if page_size < 128 or page_size & (page_size - 1) != 0:
            raise FormatError(f"bad page_size: {page_size}")
        if record_size < KEY_SIZE + 1:
            raise FormatError(f"bad record_size: {record_size}")
        rpp = records_per_page(page_size, record_size)
        if rpp < 1:
            raise FormatError("record_size exceeds page capacity")
        # 头部与记录数自洽性
        expect_pages = 1 + (num_records + rpp - 1) // rpp if num_records else 1
        if num_pages != expect_pages:
            raise FormatError(
                f"num_pages={num_pages} inconsistent with "
                f"num_records={num_records} (expect {expect_pages})"
            )
        expected_size = num_pages * page_size
        if actual_size < expected_size:
            raise TruncatedError(expected_size, actual_size, page_size)

        self.page_size = page_size
        self.record_size = record_size
        self.num_pages = num_pages
        self.num_records = num_records
        self.records_per_page = rpp

    def close(self):
        os.close(self._fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- 内部 ----

    def _read_page(self, page_no: int) -> bytes:
        if page_no < 1 or page_no >= self.num_pages:
            raise CorruptPageError(page_no, page_no * self.page_size,
                                   "page number out of range")
        cached = self._cache.get(page_no)
        if cached is not None:
            self._cache.move_to_end(page_no)
            return cached
        offset = page_no * self.page_size
        page = os.pread(self._fd, self.page_size, offset)
        self.pages_read += 1
        if len(page) < self.page_size:
            raise TruncatedError(offset + self.page_size, offset + len(page),
                                 self.page_size)
        magic, stored_no, num_in_page, payload_crc = _PAGE_HEADER_STRUCT.unpack(
            page[:PAGE_HEADER_SIZE])
        if magic != PAGE_MAGIC:
            raise CorruptPageError(page_no, offset, "bad page magic")
        if stored_no != page_no:
            raise CorruptPageError(page_no, offset,
                                   f"page number mismatch (stored {stored_no})")
        if num_in_page > self.records_per_page:
            raise CorruptPageError(page_no, offset,
                                   f"record count {num_in_page} exceeds capacity")
        payload = page[PAGE_HEADER_SIZE:
                       PAGE_HEADER_SIZE + num_in_page * self.record_size]
        if zlib.crc32(payload) & 0xFFFFFFFF != payload_crc:
            raise CorruptPageError(page_no, offset, "payload CRC mismatch")
        self._cache[page_no] = page
        if len(self._cache) > self._cache_cap:
            self._cache.popitem(last=False)
        return page

    def _key_at(self, index: int) -> int:
        page_no = 1 + index // self.records_per_page
        slot = index % self.records_per_page
        page = self._read_page(page_no)
        off = PAGE_HEADER_SIZE + slot * self.record_size
        return _KEY_STRUCT.unpack_from(page, off)[0]

    # ---- 公开 API ----

    def lookup(self, key: int):
        """精确查找，返回 payload(bytes) 或 None。O(log n) 次页读取。"""
        lo, hi = 0, self.num_records - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            k = self._key_at(mid)
            if k == key:
                return self.record_at(mid)[1]
            if k < key:
                lo = mid + 1
            else:
                hi = mid - 1
        return None

    def record_at(self, index: int):
        """按逻辑序号取 (key, payload)，payload 已去除尾部零填充无法区分，
        因此返回定长 record_size-8 的原始 payload。"""
        if index < 0 or index >= self.num_records:
            raise IndexError(f"record index {index} out of range")
        page_no = 1 + index // self.records_per_page
        slot = index % self.records_per_page
        page = self._read_page(page_no)
        off = PAGE_HEADER_SIZE + slot * self.record_size
        key = _KEY_STRUCT.unpack_from(page, off)[0]
        payload = page[off + KEY_SIZE: off + self.record_size]
        return key, payload
