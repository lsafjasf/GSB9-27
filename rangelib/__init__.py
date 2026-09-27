"""HTTP Range 请求解析与 multipart/byteranges 多段响应构造（仅标准库）。

对外 API：
    parse_range_header(header, size, max_parts) -> [(start, end), ...]  # 闭区间，已排序合并
    merge_ranges(ranges, max_parts)           -> 合并重叠/相邻区间，超限时按最小间隙继续合并
    multipart_content_length(ranges, size, content_type, boundary) -> int
    build_multipart_body(ranges, data, content_type, boundary)     -> bytes
    parse_multipart_body(body, boundary)      -> [(Content-Range 头, bytes), ...]
    RangeNotSatisfiable                       -> 对应 HTTP 416
"""
