"""区间数增长时的耗时基准。运行：python3 bench_ranges.py"""

import os
import random
import time

from http_ranges import build_multipart, multipart_content_length, parse_range_header

SIZE = 16 * 1024 * 1024  # 16 MB
COUNTS = [10, 100, 1000, 2000, 5000, 10000, 20000]
REPEAT = 5
BOUNDARY = b"benchboundary"


def make_header(n: int, size: int, seed: int = 1) -> str:
    rng = random.Random(seed)
    specs = []
    for _ in range(n):
        a = rng.randrange(0, size - 1024)
        specs.append(f"{a}-{a + rng.randrange(1, 1024)}")
    return "bytes=" + ", ".join(specs)


def main():
    data = os.urandom(SIZE)
    read = lambda s, n: data[s : s + n]
    print(f"文件大小: {SIZE} 字节, 每区间 <= 1024 字节, 每档取 {REPEAT} 次最优")
    print(f"{'区间数':>8} {'解析+合并(ms)':>14} {'构造多段体(ms)':>14} {'长度计算(ms)':>12} {'输出体(KB)':>12}")
    for n in COUNTS:
        header = make_header(n, SIZE)
        t_parse = t_build = t_len = float("inf")
        out_len = 0
        for _ in range(REPEAT):
            t0 = time.perf_counter()
            parts = parse_range_header(header, SIZE, max_parts=10**9)
            t1 = time.perf_counter()
            body = build_multipart(parts, read, SIZE, boundary=BOUNDARY)
            t2 = time.perf_counter()
            out_len = multipart_content_length(parts, SIZE, boundary=BOUNDARY)
            t3 = time.perf_counter()
            assert len(body) == out_len
            t_parse = min(t_parse, (t1 - t0) * 1e3)
            t_build = min(t_build, (t2 - t1) * 1e3)
            t_len = min(t_len, (t3 - t2) * 1e3)
        print(f"{n:>8} {t_parse:>14.3f} {t_build:>14.3f} {t_len:>12.3f} {out_len/1024:>12.1f}")


if __name__ == "__main__":
    main()
