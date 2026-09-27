"""维度退化基准：维度升高时 KD-Tree 的访问节点数与耗时变化。

数据：均匀分布 U[0,1]^d，n 个点，随机查询点，Top-K。
同时测暴力扫描耗时作对照，并统计剪枝率 = 1 - 访问节点数 / 存活节点数。

运行：python3 bench_dimensions.py [n] [queries]
"""

import random
import sys
import time

from kdtree import KDTree, brute_force

DIMS = (2, 4, 6, 8, 12, 16, 24, 32)
K = 10


def bench(dim, n, n_queries, rng):
    pts = [tuple(rng.random() for _ in range(dim)) for _ in range(n)]
    t = KDTree(dim)
    for p in pts:
        t.insert(p)
    live = t.items()
    queries = [tuple(rng.random() for _ in range(dim)) for _ in range(n_queries)]

    visited = 0
    t0 = time.perf_counter()
    for q in queries:
        got = t.query(q, K)
        visited += t.nodes_visited
    kd_time = (time.perf_counter() - t0) / n_queries

    # 抽样对拍，保证基准里的树结果也是对的
    for q in rng.sample(queries, min(5, n_queries)):
        assert [r[0] for r in t.query(q, K)] == [p for p, _ in brute_force(live, q, K)]

    t0 = time.perf_counter()
    for q in queries:
        brute_force(live, q, K)
    bf_time = (time.perf_counter() - t0) / n_queries

    avg_visited = visited / n_queries
    prune_rate = 1.0 - avg_visited / n
    return avg_visited, prune_rate, kd_time, bf_time


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    n_queries = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    rng = random.Random(42)
    print(f"n={n}, k={K}, queries={n_queries}, data ~ U[0,1]^d (均匀分布)")
    hdr = f"{'dim':>4} | {'avg访问节点':>12} | {'剪枝率':>8} | {'KD查询耗时':>12} | {'暴力扫描耗时':>12} | {'加速比':>8}"
    print(hdr)
    print("-" * len(hdr))
    for dim in DIMS:
        visited, prune, kd_t, bf_t = bench(dim, n, n_queries, rng)
        speedup = bf_t / kd_t if kd_t > 0 else float("inf")
        print(f"{dim:>4} | {visited:>12.1f} | {prune:>7.1%} | "
              f"{kd_t*1e3:>10.3f}ms | {bf_t*1e3:>10.3f}ms | {speedup:>7.2f}x")


if __name__ == "__main__":
    main()
