"""HTTP Range 请求解析与 multipart/byteranges 多段响应构造（仅标准库）。

策略说明（详见 README.md）：
- 仅支持 bytes 单位；其他单位 -> 忽略整个 Range 头（返回全量）。
- 语法非法的区间条目 -> 忽略该条目；全部非法 -> 忽略整个头（返回全量）。
- 越界处理：end 越界 -> 夹取（clamp）到 size-1；start >= size -> 丢弃该区间；
  后缀区间 -N（N>0）取末尾 N 字节，N >= size 时夹取为整文件。
- 所有区间都被丢弃 -> 抛 Unsatisfiable（对应 HTTP 416）。
- 合并：按 start 排序后合并重叠与相邻（start <= prev_end + 1）区间。
- 拆分/限数：合并后若段数超过 max_parts，反复合并间隔最小的一对，
  直到段数 <= max_parts（段数有界，防止恶意请求放大响应头开销）。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

CRLF = b"\r\n"
DEFAULT_MAX_PARTS = 1024

_RANGE_SPEC_RE = re.compile(r"^(\d*)-(\d*)$")


class Unsatisfiable(Exception):
    """所有区间都不可满足（对应 HTTP 416）。"""


@dataclass(frozen=True)
class ByteRange:
    """闭区间 [start, end]，0 <= start <= end < size，长度恒 >= 1。"""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def _parse_one(spec: str, size: int) -> "ByteRange | None":
    """解析单个区间条目。返回 None 表示该条目被忽略（非法或不可满足）。"""
    m = _RANGE_SPEC_RE.match(spec.strip())
    if not m:
        return None
    first, last = m.group(1), m.group(2)
    if first and last:
        start, end = int(first), int(last)
        if start > end:  # 零长度/反向区间：非法，忽略
            return None
        if start >= size:  # 整体越界：丢弃
            return None
        return ByteRange(start, min(end, size - 1))  # end 越界：夹取
    if first:  # "start-"：起始到末尾
        start = int(first)
        if start >= size:
            return None
        return ByteRange(start, size - 1)
    if last:  # "-N"：末尾 N 字节
        n = int(last)
        if n == 0:  # 零长度后缀：非法，忽略
            return None
        n = min(n, size)  # N 越界：夹取为整文件
        if n == 0:  # 空文件：任何后缀区间都不可满足
            return None
        return ByteRange(size - n, size - 1)
    return None  # "-" 单独出现：非法


def merge_ranges(ranges: "list[ByteRange]") -> "list[ByteRange]":
    """合并重叠与相邻区间。输入无需有序。"""
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda r: (r.start, r.end))
    merged = [ordered[0]]
    for r in ordered[1:]:
        top = merged[-1]
        if r.start <= top.end + 1:
            if r.end > top.end:
                merged[-1] = ByteRange(top.start, r.end)
        else:
            merged.append(r)
    return merged


def limit_parts(ranges: "list[ByteRange]", max_parts: int) -> "list[ByteRange]":
    """段数超过 max_parts 时，反复合并间隔最小的一对，直到段数达标。"""
    parts = list(ranges)
    while len(parts) > max_parts:
        best_i, best_gap = 0, None
        for i in range(len(parts) - 1):
            gap = parts[i + 1].start - parts[i].end - 1
            if best_gap is None or gap < best_gap:
                best_i, best_gap = i, gap
        a, b = parts[best_i], parts[best_i + 1]
        parts[best_i : best_i + 2] = [ByteRange(a.start, b.end)]
    return parts


def parse_range_header(
    header: "str | None",
    size: int,
    max_parts: int = DEFAULT_MAX_PARTS,
) -> "list[ByteRange] | None":
    """解析 Range 头。

    返回 None  -> 忽略该头，应返回全量响应（200）。
    返回列表   -> 已合并、限数、按 start 升序的区间（206）。
    抛 Unsatisfiable -> 应返回 416。
    """
    if header is None:
        return None
    unit, sep, spec_set = header.partition("=")
    if not sep or unit.strip().lower() != "bytes":
        return None  # 不认识的单位：忽略整个头
    ranges = []
    for spec in spec_set.split(","):
        r = _parse_one(spec, size)
        if r is not None:
            ranges.append(r)
    if not ranges:
        if size == 0 or spec_set.strip():
            # 头里确实写了区间但全部不可满足 -> 416；空文件上任何区间都不可满足
            raise Unsatisfiable(f"no satisfiable range for size {size}")
        return None  # "bytes=" 空集合：忽略
    return limit_parts(merge_ranges(ranges), max_parts)


def _part_header(boundary: bytes, content_type: str, r: ByteRange, size: int) -> bytes:
    return (
        b"--" + boundary + CRLF
        + b"Content-Type: " + content_type.encode("ascii") + CRLF
        + b"Content-Range: bytes %d-%d/%d" % (r.start, r.end, size) + CRLF
        + CRLF
    )


def build_multipart(
    parts: "list[ByteRange]",
    read_range,
    size: int,
    content_type: str = "application/octet-stream",
    boundary: "bytes | None" = None,
) -> bytes:
    """构造 multipart/byteranges 响应体。

    read_range(start, length) -> bytes：按区间读取数据的回调（可来自文件/内存）。
    """
    if boundary is None:
        boundary = uuid.uuid4().hex.encode("ascii")
    out = bytearray()
    for r in parts:
        out += _part_header(boundary, content_type, r, size)
        out += read_range(r.start, r.length)
        out += CRLF
    out += b"--" + boundary + b"--" + CRLF
    return bytes(out)


def multipart_content_length(
    parts: "list[ByteRange]",
    size: int,
    content_type: str = "application/octet-stream",
    boundary: "bytes | None" = None,
) -> int:
    """不构造响应体，精确计算 multipart 体的总字节数（用于 Content-Length）。"""
    if boundary is None:
        boundary = uuid.uuid4().hex.encode("ascii")
    total = len(boundary) + 4 + 2  # "--" + boundary + "--" + CRLF
    for r in parts:
        total += len(_part_header(boundary, content_type, r, size)) + r.length + 2
    return total


def parse_multipart(body: bytes, boundary: bytes) -> "list[tuple[int, int, bytes]]":
    """解析 multipart/byteranges 体，返回 [(start, end, data), ...]（自测/客户端用）。"""
    delimiter = b"--" + boundary
    segments = body.split(delimiter)
    results = []
    for seg in segments[1:]:
        if seg.startswith(b"--"):  # 结束标记
            break
        if seg.startswith(CRLF):
            seg = seg[len(CRLF):]
        header_blob, sep, data = seg.partition(CRLF + CRLF)
        if not sep:
            raise ValueError("malformed part: missing header/body separator")
        start = end = None
        for line in header_blob.split(CRLF):
            if line.lower().startswith(b"content-range:"):
                m = re.search(rb"bytes (\d+)-(\d+)/(\d+|\*)", line)
                if not m:
                    raise ValueError("malformed Content-Range")
                start, end = int(m.group(1)), int(m.group(2))
        if start is None:
            raise ValueError("part missing Content-Range")
        if data.endswith(CRLF):
            data = data[: -len(CRLF)]
        if len(data) != end - start + 1:
            raise ValueError("part length mismatch")
        results.append((start, end, data))
    return results
