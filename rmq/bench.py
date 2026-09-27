"""基准：预处理耗时、百万次随机查询耗时、结构内存占用。

运行：python3 bench.py [n] [queries]
"""

import random
import resource
import sys
import time

from rmq import SparseTable


def main(n=1_000_000, q=1_000_000):
    rng = random.Random(42)
    arr = [rng.randrange(1 << 30) for _ in range(n)]

    t0 = time.perf_counter()
    st = SparseTable(arr)
    t1 = time.perf_counter()
    print(f"数组长度 n        = {n:,}")
    print(f"预处理耗时        = {t1 - t0:.3f} s")
    print(f"结构内存(估算)    = {st.memory_bytes() / 2**20:.1f} MiB"
          f"（约 {st.memory_bytes() / n:.1f} 字节/元素）")

    queries = [(lambda a, b: (a, b) if a <= b else (b, a))
               (rng.randrange(n), rng.randrange(n)) for _ in range(q)]

    t0 = time.perf_counter()
    s = 0
    for l, r in queries:
        s += st.min_index(l, r)
    t1 = time.perf_counter()
    for l, r in queries:
        s += st.max_index(l, r)
    t2 = time.perf_counter()
    print(f"min 查询 {q:,} 次 = {t1 - t0:.3f} s（{(t1 - t0) / q * 1e6:.2f} us/次）")
    print(f"max 查询 {q:,} 次 = {t2 - t1:.3f} s（{(t2 - t1) / q * 1e6:.2f} us/次）")
    print(f"校验和            = {s}（防优化占位）")
    print(f"进程峰值内存      = {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.1f} MiB")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    q = int(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000
    main(n, q)
