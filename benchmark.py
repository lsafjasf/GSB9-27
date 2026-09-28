"""EDD 调度耗时基准：上千/上万任务规模。运行: python3 benchmark.py"""

import random
import time

from edd import Job, lower_bound, max_lateness, schedule


def bench(n, seed=42):
    rng = random.Random(seed)
    jobs = [Job(i, rng.randint(1, 1000), rng.randint(1, 10 * n)) for i in range(n)]
    t0 = time.perf_counter()
    pl = schedule(jobs)
    lmax = max_lateness(pl)
    t1 = time.perf_counter()
    lb, _ = lower_bound(jobs)
    print(f"n={n:>7}  排序+排产耗时 {(t1 - t0) * 1000:8.2f} ms   Lmax={lmax}  LB={lb}")


if __name__ == "__main__":
    for n in (1_000, 5_000, 10_000, 50_000, 100_000):
        bench(n)
