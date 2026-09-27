"""Benchmark CLI: build index files, measure RSS and query latency.

Usage:
    python3 -m paged_index.bench build --path f.idx --records 1000000
    python3 -m paged_index.bench run   --path f.idx --queries 100000
"""

import argparse
import json
import random
import resource
import time

from .builder import build_index
from .reader import PagedIndexReader


def vm_rss_kb():
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return -1


def max_rss_kb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def cmd_build(args):
    def records():
        for i in range(args.records):
            key = i * 2 + 1  # odd keys; even keys are guaranteed misses
            yield key, key * 0x9E3779B97F4A7C15 % (1 << 64)

    n = build_index(
        args.path, records(), page_size=args.page_size, record_size=args.record_size
    )
    import os

    print(
        json.dumps(
            {"path": args.path, "records": n, "file_bytes": os.path.getsize(args.path)}
        )
    )


def percentiles(sorted_samples, points):
    out = {}
    n = len(sorted_samples)
    for p in points:
        rank = min(n - 1, max(0, round(p / 100 * (n - 1))))
        out[f"p{p}"] = sorted_samples[rank]
    return out


def cmd_run(args):
    rng = random.Random(args.seed)
    with PagedIndexReader(args.path, cache_pages=args.cache_pages) as reader:
        n = len(reader)
        # 90% present keys, 10% guaranteed misses (even keys)
        queries = []
        for _ in range(args.queries):
            if rng.random() < 0.9:
                queries.append(rng.randrange(n) * 2 + 1)
            else:
                queries.append(rng.randrange(n + 1) * 2)

        rss_before = vm_rss_kb()
        lat_ns = []
        found = 0
        t0 = time.perf_counter()
        for key in queries:
            s = time.perf_counter_ns()
            value = reader.lookup(key)
            lat_ns.append(time.perf_counter_ns() - s)
            if value is not None:
                found += 1
        total_s = time.perf_counter() - t0
        rss_after = vm_rss_kb()

    lat_us = sorted(x / 1000.0 for x in lat_ns)
    result = {
        "path": args.path,
        "records": n,
        "queries": len(queries),
        "found": found,
        "cache_pages": args.cache_pages,
        "cache_hits": reader.hits,
        "cache_misses": reader.misses,
        "cache_hit_rate": round(reader.hits / max(1, reader.hits + reader.misses), 4),
        "rss_before_kb": rss_before,
        "rss_after_kb": rss_after,
        "rss_delta_kb": rss_after - rss_before,
        "max_rss_kb": max_rss_kb(),
        "total_seconds": round(total_s, 3),
        "queries_per_second": round(len(queries) / total_s, 1),
        "latency_us": {
            k: round(v, 2)
            for k, v in percentiles(lat_us, [50, 90, 99, 99.9, 100]).items()
        },
        "latency_mean_us": round(sum(lat_us) / len(lat_us), 2),
    }
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(prog="paged_index.bench")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build")
    b.add_argument("--path", required=True)
    b.add_argument("--records", type=int, required=True)
    b.add_argument("--page-size", type=int, default=4096)
    b.add_argument("--record-size", type=int, default=16)
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("run")
    r.add_argument("--path", required=True)
    r.add_argument("--queries", type=int, default=100_000)
    r.add_argument("--cache-pages", type=int, default=128)
    r.add_argument("--seed", type=int, default=42)
    r.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
