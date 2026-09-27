"""benchmark.py -- 规模压测：耗时与内存。

用法：
  python3 benchmark.py            # 跑完整阶梯（每个规模在独立子进程中测量）
  python3 benchmark.py --child N METHOD   # 子进程模式（内部使用）

每个规模输出：耗时（秒）、峰值 RSS（MB）、距离矩阵理论大小（MB）。
"""

import random
import resource
import subprocess
import sys
import time

from hclust import linkage, linkage_naive

SEED = 42


def gen_points(n, dim=2):
    rng = random.Random(SEED)
    # 多峰分布，模拟真实聚类场景
    centers = [(rng.uniform(0, 1000), rng.uniform(0, 1000)) for _ in range(20)]
    pts = []
    for i in range(n):
        cx, cy = centers[i % len(centers)]
        pts.append((cx + rng.gauss(0, 30), cy + rng.gauss(0, 30)))
    return pts


def run_one(n, method, naive=False):
    pts = gen_points(n)
    t0 = time.perf_counter()
    merges = linkage_naive(pts, method) if naive else linkage(pts, method)
    dt = time.perf_counter() - t0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # MB
    assert len(merges) == max(0, n - 1)
    return dt, rss


def child(argv):
    n = int(argv[0])
    method = argv[1]
    naive = len(argv) > 2 and argv[2] == "naive"
    dt, rss = run_one(n, method, naive)
    print("RESULT %.3f %.1f" % (dt, rss))


def matrix_mb(n):
    return n * (n - 1) // 2 * 8 / 1e6


def main():
    print("== 快速实现（NN-chain + Lance-Williams, O(n^2)）==")
    print("%8s %8s %10s %12s %14s" % ("n", "method", "time(s)", "peakRSS(MB)", "matrix(MB)"))
    for n in (1000, 2000, 4000, 6000, 8000, 10000, 12000):
        for method in ("single", "average"):
            out = subprocess.check_output(
                [sys.executable, __file__, "--child", str(n), method]).decode()
            _, dt, rss = out.split()
            print("%8d %8s %10s %12s %14.0f" % (n, method, dt, rss, matrix_mb(n)))
            sys.stdout.flush()
    print()
    print("== 朴素实现（每步全量重算, O(n^3) 时间 / O(n^2) 距离重算）==")
    print("%8s %8s %10s %12s" % ("n", "method", "time(s)", "peakRSS(MB)"))
    for n in (100, 200, 400):
        for method in ("single", "average"):
            out = subprocess.check_output(
                [sys.executable, __file__, "--child", str(n), method, "naive"]).decode()
            _, dt, rss = out.split()
            print("%8d %8s %10s %12s" % (n, method, dt, rss))
            sys.stdout.flush()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(sys.argv[2:])
    else:
        main()
