"""Scale benchmark: time and memory for hclust.py (and the naive reference).

Usage:
    python3 benchmark.py            # fast implementation, 1k .. 12k points
    python3 benchmark.py --naive    # naive reference, small sizes only
"""

import random
import resource
import sys
import time

from hclust import hierarchical_clustering
from naive_hclust import naive_global


def make_points(n, dim, seed=42):
    rng = random.Random(seed)
    return [[rng.random() * 1000 for _ in range(dim)] for _ in range(n)]


def bench_fast(sizes, dim=2):
    print("fast NN-chain implementation (dim=%d)" % dim)
    print("%7s %8s | %10s %10s | %12s %12s" % (
        "n", "merges", "single(s)", "average(s)",
        "peak MB", "matrix MB"))
    for n in sizes:
        pts = make_points(n, dim)
        row = []
        for linkage in ("single", "average"):
            t0 = time.perf_counter()
            merges = hierarchical_clustering(pts, linkage)
            dt = time.perf_counter() - t0
            row.append((dt, len(merges)))
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        matrix_mb = (n * (n - 1) // 2) * 8 / 1e6
        print("%7d %8d | %10.2f %10.2f | %12.1f %12.1f" % (
            n, row[0][1], row[0][0], row[1][0],
            rss_kb / 1024, matrix_mb))
        sys.stdout.flush()


def bench_naive(sizes, dim=2):
    print("naive reference (full recompute every step, dim=%d)" % dim)
    print("%7s | %10s %10s" % ("n", "single(s)", "average(s)"))
    for n in sizes:
        pts = make_points(n, dim)
        out = []
        for linkage in ("single", "average"):
            t0 = time.perf_counter()
            naive_global(pts, linkage)
            out.append(time.perf_counter() - t0)
        print("%7d | %10.2f %10.2f" % (n, out[0], out[1]))
        sys.stdout.flush()


if __name__ == "__main__":
    if "--naive" in sys.argv:
        bench_naive([50, 100, 200, 400])
    else:
        bench_fast([1000, 2000, 4000, 8000, 12000])
