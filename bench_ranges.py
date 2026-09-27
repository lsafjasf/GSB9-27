"""区间数增长时的耗时基准：解析+合并、多段构造、长度预算、还原解析。

运行：python3 bench_ranges.py
"""

import random
import time

from rangelib.core import (
    build_multipart_body,
    merge_ranges,
    multipart_content_length,
    parse_multipart_body,
    parse_range_header,
)

SIZE = 16 * 1024 * 1024  # 16 MiB 资源
CT = "application/octet-stream"


def make_header(n, size, rng):
    specs = []
    for _ in range(n):
        kind = rng.randint(0, 2)
        if kind == 0:
            a = rng.randint(0, size - 1)
            b = min(size - 1, a + rng.randint(0, 4096))
            specs.append("%d-%d" % (a, b))
        elif kind == 1:
            specs.append("%d-" % rng.randint(0, size - 1))
        else:
            specs.append("-%d" % rng.randint(1, 8192))
    return "bytes=" + ",".join(specs)


def make_disjoint_header(n, size):
    # 均匀散布的 100 字节区间，保证互不重叠、不会被合并
    step = size // n
    return "bytes=" + ",".join("%d-%d" % (i * step, i * step + 99)
                               for i in range(n))


def bench():
    rng = random.Random(42)
    data = rng.randbytes(SIZE)
    print("资源大小: %d MiB" % (SIZE // 1024 // 1024))
    print("场景 A：随机区间（大量重叠，max_parts=10000）")
    print("%-8s %-8s %12s %12s %12s %12s" %
          ("区间数", "合并后", "解析+合并ms", "构造body ms", "长度预算ms", "还原解析ms"))
    for n in (10, 100, 1000, 5000, 10000, 20000):
        header = make_header(n, SIZE, rng)

        t0 = time.perf_counter()
        ranges = parse_range_header(header, SIZE, max_parts=10000)
        t1 = time.perf_counter()
        body = build_multipart_body(ranges, data, CT)
        t2 = time.perf_counter()
        length = multipart_content_length(ranges, SIZE, CT)
        t3 = time.perf_counter()
        parts = parse_multipart_body(body, "py-range-boundary-7MA4YWxkTrZu0gW")
        t4 = time.perf_counter()

        assert length == len(body)
        assert b"".join(p for _, p in parts) == b"".join(
            data[s:e + 1] for s, e in ranges)
        print("%-8d %-8d %12.2f %12.2f %12.2f %12.2f" %
              (n, len(ranges), (t1 - t0) * 1e3, (t2 - t1) * 1e3,
               (t3 - t2) * 1e3, (t4 - t3) * 1e3))

    print()
    print("场景 B：互不相交的 100 字节区间（合并后段数 = 区间数）")
    print("%-8s %-8s %12s %12s %12s %12s" %
          ("区间数", "合并后", "解析+合并ms", "构造body ms", "长度预算ms", "还原解析ms"))
    for n in (10, 100, 1000, 5000, 10000, 20000):
        header = make_disjoint_header(n, SIZE)

        t0 = time.perf_counter()
        ranges = parse_range_header(header, SIZE, max_parts=100000)
        t1 = time.perf_counter()
        body = build_multipart_body(ranges, data, CT)
        t2 = time.perf_counter()
        length = multipart_content_length(ranges, SIZE, CT)
        t3 = time.perf_counter()
        parts = parse_multipart_body(body, "py-range-boundary-7MA4YWxkTrZu0gW")
        t4 = time.perf_counter()

        assert length == len(body)
        assert b"".join(p for _, p in parts) == b"".join(
            data[s:e + 1] for s, e in ranges)
        print("%-8d %-8d %12.2f %12.2f %12.2f %12.2f" %
              (n, len(ranges), (t1 - t0) * 1e3, (t2 - t1) * 1e3,
               (t3 - t2) * 1e3, (t4 - t3) * 1e3))


if __name__ == "__main__":
    bench()
