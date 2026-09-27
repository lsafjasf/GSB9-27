"""Dimension-scaling benchmark: visited nodes & query time vs brute force.

Run: python3 benchmark.py
"""

import random
import time

from kdtree import KDTree, brute_force_topk

N = 20000          # dataset size
K = 10             # top-k
QUERIES = 30       # queries per dimension
DIMS = (2, 4, 8, 12, 16, 24, 32, 48, 64)


def bench(dim):
    rng = random.Random(1234 + dim)
    points = [tuple(rng.random() for _ in range(dim)) for _ in range(N)]
    queries = [tuple(rng.random() for _ in range(dim)) for _ in range(QUERIES)]
    tree = KDTree.build(points)

    visited_total = 0
    t0 = time.perf_counter()
    for q in queries:
        _, visited = tree.query_with_stats(q, K)
        visited_total += visited
    tree_ms = (time.perf_counter() - t0) / QUERIES * 1000

    t0 = time.perf_counter()
    for q in queries:
        brute_force_topk(points, q, K)
    bf_ms = (time.perf_counter() - t0) / QUERIES * 1000

    avg_visited = visited_total / QUERIES
    return avg_visited, avg_visited / N * 100, tree_ms, bf_ms


def main():
    print(f"n={N}, k={K}, queries={QUERIES} per dim, uniform random in unit hypercube")
    print()
    print("| dim | visited nodes | visited % | tree ms/query | brute-force ms/query | speedup |")
    print("|----:|--------------:|----------:|--------------:|---------------------:|--------:|")
    for dim in DIMS:
        visited, pct, tree_ms, bf_ms = bench(dim)
        speedup = bf_ms / tree_ms if tree_ms > 0 else float("inf")
        print(f"| {dim:>3} | {visited:>13.0f} | {pct:>8.1f}% | {tree_ms:>13.2f} | {bf_ms:>19.2f} | {speedup:>6.2f}x |")


if __name__ == "__main__":
    main()
