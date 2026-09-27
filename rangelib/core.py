"""Range 头解析、区间合并/拆分、multipart/byteranges 构造与解析。

策略（详见 README.md）：
- 越界：夹取（clamp）。end 超过文件末尾时夹到 size-1；start >= size 的区间不可满足，忽略。
- 非法/零长度区间（start > end、后缀长度 <= 0、语法错误）：忽略该条，不整体拒绝。
- 全部区间被忽略后没有可服务区间：抛 RangeNotSatisfiable（HTTP 416）。
- 空文件（size == 0）：任何 Range 都不可满足，抛 RangeNotSatisfiable。
- 合并：重叠或相邻（next.start <= prev.end + 1）的区间合并为一段，减少分片数。
- 拆分/限流：合并后仍超过 max_parts 时，反复合并"间隙最小"的相邻两段，
  直到段数 <= max_parts（宁可多传间隙字节，也不让分片数失控）。
"""

from __future__ import annotations

import re

DEFAULT_MAX_PARTS = 64
DEFAULT_BOUNDARY = "py-range-boundary-7MA4YWxkTrZu0gW"

_RANGE_SPEC_RE = re.compile(r"^(\d*)-(\d*)$")


class RangeNotSatisfiable(Exception):
    """没有任何可满足的区间，对应 HTTP 416。"""

    def __init__(self, size: int):
        super().__init__("no satisfiable range (size=%d)" % size)
        self.size = size


def _parse_one_spec(spec: str, size: int):
    """解析单个区间写法，返回 (start, end) 闭区间；不可满足/非法返回 None。

    支持三种写法：
        a-b   起始到结束（end 越界夹取到 size-1）
        a-    起始到末尾
        -n    末尾 n 个字节（n > size 时夹取为整文件）
    """
    spec = spec.strip()
    match = _RANGE_SPEC_RE.match(spec)
    if match is None:
        return None
    first, last = match.group(1), match.group(2)
    if first == "" and last == "":
        return None
    if first == "":
        # 后缀区间：末尾 n 字节
        suffix = int(last)
        if suffix <= 0:  # 零长度后缀，忽略
            return None
        start = max(0, size - suffix)
        return (start, size - 1)
    start = int(first)
    if start >= size:  # 起点越界，整体不可满足，忽略该条
        return None
    end = size - 1 if last == "" else min(int(last), size - 1)
    if end < start:  # 零长度/反向区间，忽略
        return None
    return (start, end)


def merge_ranges(ranges, max_parts: int = DEFAULT_MAX_PARTS):
    """合并重叠/相邻区间；段数超过 max_parts 时按最小间隙继续合并。"""
    sorted_ranges = sorted(ranges)
    merged = []
    for start, end in sorted_ranges:
        if merged and start <= merged[-1][1] + 1:
            last = merged[-1]
            merged[-1] = (last[0], max(last[1], end))
        else:
            merged.append((start, end))
    while len(merged) > max_parts:
        # 找间隙最小的相邻两段合并（把间隙字节也传过去）
        best_i, best_gap = 0, None
        for i in range(len(merged) - 1):
            gap = merged[i + 1][0] - merged[i][1] - 1
            if best_gap is None or gap < best_gap:
                best_i, best_gap = i, gap
        a, b = merged[best_i], merged[best_i + 1]
        merged[best_i:best_i + 2] = [(a[0], b[1])]
    return merged


def parse_range_header(header: str, size: int,
                       max_parts: int = DEFAULT_MAX_PARTS):
    """解析 Range 头，返回排序合并后的闭区间列表。

    header: 形如 "bytes=0-99, 200-, -50"（大小写不敏感，unit 必须是 bytes）
    size:   资源总字节数
    无有效区间时抛 RangeNotSatisfiable。
    """
    if size <= 0:
        raise RangeNotSatisfiable(size)
    if header is None:
        raise ValueError("header is None")
    unit, sep, spec_list = header.partition("=")
    if sep == "" or unit.strip().lower() != "bytes":
        raise ValueError("unsupported range unit: %r" % header)
    ranges = []
    for spec in spec_list.split(","):
        parsed = _parse_one_spec(spec, size)
        if parsed is not None:
            ranges.append(parsed)
    if not ranges:
        raise RangeNotSatisfiable(size)
    return merge_ranges(ranges, max_parts=max_parts)


def _part_header(content_type: str, start: int, end: int, size: int) -> bytes:
    return (
        "Content-Type: %s\r\n"
        "Content-Range: bytes %d-%d/%d\r\n"
        "\r\n" % (content_type, start, end, size)
    ).encode("ascii")


def multipart_content_length(ranges, size: int, content_type: str,
                             boundary: str = DEFAULT_BOUNDARY) -> int:
    """不构造 body，直接计算 multipart 响应体的总字节数（Content-Length）。"""
    boundary_b = boundary.encode("ascii")
    total = 0
    for start, end in ranges:
        total += 2 + len(boundary_b) + 2          # "--" boundary "\r\n"
        total += len(_part_header(content_type, start, end, size))
        total += end - start + 1                   # 数据
        total += 2                                 # 数据后的 "\r\n"
    total += 2 + len(boundary_b) + 2 + 2           # "--" boundary "--" "\r\n"
    return total


def build_multipart_body(ranges, data: bytes, content_type: str,
                         boundary: str = DEFAULT_BOUNDARY) -> bytes:
    """按区间从 data（完整资源字节）切片，构造 multipart/byteranges 响应体。"""
    size = len(data)
    chunks = []
    for start, end in ranges:
        if not (0 <= start <= end < size):
            raise ValueError("range (%d, %d) out of bounds for size %d"
                             % (start, end, size))
        chunks.append(b"--" + boundary.encode("ascii") + b"\r\n")
        chunks.append(_part_header(content_type, start, end, size))
        chunks.append(data[start:end + 1])
        chunks.append(b"\r\n")
    chunks.append(b"--" + boundary.encode("ascii") + b"--\r\n")
    return b"".join(chunks)


def multipart_content_type(boundary: str = DEFAULT_BOUNDARY) -> str:
    return "multipart/byteranges; boundary=%s" % boundary


_CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


def parse_multipart_body(body: bytes, boundary: str):
    """解析 multipart/byteranges 响应体，返回 [((start, end, size), bytes), ...]。

    用于测试对拍：把多段响应还原后与原始资源逐字节比对。
    """
    delimiter = b"--" + boundary.encode("ascii")
    sections = body.split(delimiter)
    # sections[0] 应为空（前导），最后一段是 "--\r\n"
    if sections[0].strip():
        raise ValueError("unexpected preamble before first boundary")
    if sections[-1].strip() != b"--":
        raise ValueError("missing closing boundary")
    parts = []
    for section in sections[1:-1]:
        if not section.startswith(b"\r\n"):
            raise ValueError("malformed part start")
        header_blob, sep, payload = section[2:].partition(b"\r\n\r\n")
        if sep == b"":
            raise ValueError("malformed part: no header/body separator")
        if not payload.endswith(b"\r\n"):
            raise ValueError("malformed part: no trailing CRLF")
        payload = payload[:-2]
        content_range = None
        for line in header_blob.split(b"\r\n"):
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-range":
                content_range = value.strip().decode("ascii")
        if content_range is None:
            raise ValueError("part missing Content-Range")
        match = _CONTENT_RANGE_RE.match(content_range)
        if match is None:
            raise ValueError("bad Content-Range: %r" % content_range)
        start, end, size = (int(match.group(i)) for i in (1, 2, 3))
        if len(payload) != end - start + 1:
            raise ValueError("part length mismatch with Content-Range")
        parts.append(((start, end, size), payload))
    return parts
