"""压测：n = 1,000,000，随机 1,000,000 次查询，报告预处理/查询耗时与内存。

运行：python3 bench_rmq.py
"""

import random
import resource
import time

from rmq import SparseTableRMQ

N = 1_000_000
Q = 1_000_000


def rss_mb():
    # Linux: ru_maxrss 单位为 KB
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def main():
    rng = random.Random(42)
    data = [rng.randint(-10**9, 10**9) for _ in range(N)]

    t0 = time.perf_counter()
    st = SparseTableRMQ(data)
    t1 = time.perf_counter()
    build_s = t1 - t0

    mem_table_mb = st.memory_bytes() / 1024 / 1024
    mem_rss_mb = rss_mb()

    queries = [(lambda l: (l, l + rng.randrange(N - l)))(rng.randrange(N))
               for _ in range(Q)]

    t2 = time.perf_counter()
    checksum = 0
    rmin = st.range_min
    rmax = st.range_max
    for l, r in queries:
        checksum ^= rmin(l, r)[1]
        checksum ^= rmax(l, r)[1]
    t3 = time.perf_counter()
    query_s = t3 - t2

    print(f"数组规模 n        : {N:,}")
    print(f"预处理耗时        : {build_s:.2f} s")
    print(f"稀疏表内存(理论)  : {mem_table_mb:.1f} MiB  (2 张表 x n*log2(n) 项 x 4 B)")
    print(f"进程峰值 RSS      : {mem_rss_mb:.1f} MiB  (含 data、查询列表等全部开销)")
    print(f"查询次数          : {Q:,} 次 (min+max 各一次)")
    print(f"查询总耗时        : {query_s:.2f} s")
    print(f"单次查询均耗      : {query_s / Q * 1e6:.2f} us/次 (min+max 合计)")
    print(f"checksum          : {checksum} (防优化占位)")


if __name__ == "__main__":
    main()
