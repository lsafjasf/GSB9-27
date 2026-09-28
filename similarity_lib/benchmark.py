"""性能基准：每个度量跑 1,000,000 次计算，输出总耗时与单次耗时。

运行：python3 benchmark.py
"""

import random
import time

import similarity as sim

N = 1_000_000
POOL = 2_000  # 数据池大小：循环复用，避免预生成百万条数据撑爆内存扭曲计时


def bench(name, fn, pairs):
    start = time.perf_counter()
    total = 0.0
    n_pairs = len(pairs)
    for i in range(N):
        a, b = pairs[i % n_pairs]
        total += fn(a, b)
    elapsed = time.perf_counter() - start
    print(f"{name:<28} {N:>9,} 次  总耗时 {elapsed:7.3f}s  "
          f"单次 {elapsed / N * 1e6:8.3f} µs  (校验和 {total:.6f})")
    return elapsed


def main():
    rng = random.Random(2024)

    # 稠密：64 维
    dense_pairs = [
        ([rng.uniform(-1, 1) for _ in range(64)],
         [rng.uniform(-1, 1) for _ in range(64)])
        for _ in range(POOL)
    ]
    # 稀疏：1000 维、95% 为零，dict 表示
    sparse_pairs = []
    for _ in range(POOL):
        a = {i: rng.uniform(-1, 1) for i in range(1000) if rng.random() > 0.95}
        b = {i: rng.uniform(-1, 1) for i in range(1000) if rng.random() > 0.95}
        sparse_pairs.append((a, b))
    # 集合：50 元素池
    set_pairs = [
        ({rng.randrange(50) for _ in range(15)},
         {rng.randrange(50) for _ in range(15)})
        for _ in range(POOL)
    ]

    print(f"各度量 {N:,} 次计算耗时（Python {__import__('sys').version.split()[0]}）\n")
    print("== 稠密 64 维 ==")
    bench("cosine_similarity", sim.cosine_similarity, dense_pairs)
    bench("euclidean_distance", sim.euclidean_distance, dense_pairs)
    bench("manhattan_distance", sim.manhattan_distance, dense_pairs)
    print("\n== 稀疏 1000 维（95% 为零，dict 表示）==")
    bench("cosine_similarity", sim.cosine_similarity, sparse_pairs)
    bench("euclidean_distance", sim.euclidean_distance, sparse_pairs)
    bench("manhattan_distance", sim.manhattan_distance, sparse_pairs)
    print("\n== 集合（15 元素 / 50 元素池）==")
    bench("jaccard_similarity", sim.jaccard_similarity, set_pairs)
    bench("jaccard_distance", sim.jaccard_distance, set_pairs)


if __name__ == "__main__":
    main()
