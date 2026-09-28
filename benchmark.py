"""Performance benchmark: 100k points, stdlib only.

Run: python3 benchmark.py
"""

import resource
import time
import tracemalloc
import random

from clustering import kmeans, choose_k


def make_blobs(n, dim, k, seed=42, gap=40.0):
    rng = random.Random(seed)
    centers = [[rng.uniform(-gap, gap) for _ in range(dim)] for _ in range(k)]
    pts = []
    for i in range(n):
        c = centers[i % k]
        pts.append([x + rng.gauss(0.0, 1.0) for x in c])
    rng.shuffle(pts)
    return pts


def report(tag, fn):
    tracemalloc.start()
    t0 = time.perf_counter()
    res = fn()
    dt = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    print("%-34s %8.2f s   traced peak %8.1f MiB   max RSS %7.1f MiB"
          % (tag, dt, peak / 2**20, rss_mb))
    return res


def main():
    n, dim, k = 100_000, 4, 5
    print("dataset: %d points, %d dims, %d true blobs" % (n, dim, k))
    pts = make_blobs(n, dim, k)

    r = report("kmeans k=5 on 100k points",
               lambda: kmeans(pts, k))
    print("  -> iterations=%d converged=%s inertia=%.1f empty_events=%d"
          % (r.iterations, r.converged, r.inertia, r.empty_cluster_events))

    sub = pts[:10_000]
    sel = report("choose_k k=2..8 on 10k subset",
                 lambda: choose_k(sub, k_max=8))
    print("  -> selected k=%d (%s)" % (sel.k, sel.reason))


if __name__ == "__main__":
    main()
