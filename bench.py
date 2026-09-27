"""基准：随机查询 10 万次，验证 RSS 不随文件大小线性增长，并输出延迟分布。

用法: python3 bench.py [--queries 100000] [--sizes-mb 4,32,128] [--cache-pages 16]
"""

import argparse
import gc
import os
import random
import struct
import sys
import tempfile
import time

import paged_index as pi

PAGE_SIZE = 4096
RECORD_SIZE = 16
STRIDE = 10  # key = i * STRIDE，查询 key 空间 = num_records * STRIDE


def rss_kb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS"):
                return int(line.split()[1])
    return -1


def gen_records(n):
    for i in range(n):
        yield i * STRIDE, struct.pack(">Q", i)


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def run_one(path, target_bytes, queries, cache_pages, seed=42):
    num_records = max(1, target_bytes // RECORD_SIZE)
    if not os.path.exists(path):
        st = pi.build_index(path, gen_records(num_records),
                            page_size=PAGE_SIZE, record_size=RECORD_SIZE,
                            presorted=True)
    else:
        st = {"num_records": num_records}
    file_size = os.path.getsize(path)

    gc.collect()
    rss_before = rss_kb()
    lat = []
    rng = random.Random(seed)
    key_space = st["num_records"] * STRIDE
    hits = 0
    with pi.PagedIndexReader(path, cache_pages=cache_pages) as r:
        for _ in range(min(2000, queries)):  # 预热
            r.lookup(rng.randrange(key_space))
        gc.collect()
        rss_warm = rss_kb()
        for _ in range(queries):
            k = rng.randrange(key_space)
            t0 = time.perf_counter_ns()
            got = r.lookup(k)
            lat.append(time.perf_counter_ns() - t0)
            hits += got is not None
        pages_read = r.pages_read
    gc.collect()
    rss_after = rss_kb()

    lat.sort()
    us = [x / 1000.0 for x in lat]
    return {
        "file_mb": file_size / 1e6,
        "num_records": st["num_records"],
        "rss_before_kb": rss_before,
        "rss_warm_kb": rss_warm,
        "rss_after_kb": rss_after,
        "pages_read": pages_read,
        "hit_rate": hits / queries,
        "p50_us": percentile(us, 0.50),
        "p90_us": percentile(us, 0.90),
        "p99_us": percentile(us, 0.99),
        "p999_us": percentile(us, 0.999),
        "max_us": us[-1],
        "mean_us": sum(us) / len(us),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=100_000)
    ap.add_argument("--sizes-mb", default="4,32,128")
    ap.add_argument("--cache-pages", type=int, default=16)
    ap.add_argument("--keep", action="store_true", help="保留生成的索引文件")
    args = ap.parse_args()

    sizes = [float(x) for x in args.sizes_mb.split(",")]
    tmp = tempfile.mkdtemp(prefix="paged_idx_bench_")
    print(f"workdir: {tmp}")
    print(f"queries per size: {args.queries}, page cache: {args.cache_pages} 页 "
          f"({args.cache_pages * PAGE_SIZE / 1024:.0f} KiB)")
    print()
    hdr = (f"{'file_MB':>9} {'records':>10} {'RSS前_KiB':>10} {'RSS后_KiB':>10} "
           f"{'ΔRSS_KiB':>9} {'读页数':>9} {'p50_µs':>8} {'p90_µs':>8} "
           f"{'p99_µs':>8} {'p99.9_µs':>9} {'max_µs':>9}")
    print(hdr)
    print("-" * len(hdr))
    results = []
    for mb in sizes:
        path = os.path.join(tmp, f"idx_{mb:g}mb.bin")
        res = run_one(path, int(mb * 1e6), args.queries, args.cache_pages)
        results.append(res)
        print(f"{res['file_mb']:9.1f} {res['num_records']:10d} "
              f"{res['rss_warm_kb']:10d} {res['rss_after_kb']:10d} "
              f"{res['rss_after_kb'] - res['rss_warm_kb']:9d} "
              f"{res['pages_read']:9d} "
              f"{res['p50_us']:8.2f} {res['p90_us']:8.2f} {res['p99_us']:8.2f} "
              f"{res['p999_us']:9.2f} {res['max_us']:9.2f}")
    print()
    print(f"命中率: {results[-1]['hit_rate']:.1%}（key 空间含空隙，未命中也走完整二分）")
    growth = results[-1]["file_mb"] / max(results[0]["file_mb"], 1e-9)
    drss = results[-1]["rss_after_kb"] - results[0]["rss_after_kb"]
    print(f"文件增大 {growth:.0f}x，末行 RSS 差 {drss} KiB"
          f"（页缓存上限恒定 {args.cache_pages * PAGE_SIZE / 1024:.0f} KiB）")
    if not args.keep:
        for mb in sizes:
            os.unlink(os.path.join(tmp, f"idx_{mb:g}mb.bin"))
        os.rmdir(tmp)
    else:
        print(f"索引文件保留在: {tmp}")


if __name__ == "__main__":
    sys.exit(main())
