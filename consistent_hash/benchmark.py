"""实测：迁移比例 vs 理论下限、虚拟节点数 vs 分布偏差、百万键查询耗时。"""
import statistics
import time

from consistent_hash import HashRing


def make_keys(n):
    return [f"key-{i}" for i in range(n)]


def bench_migration(n_nodes=10, n_keys=200_000, vnodes=160):
    keys = make_keys(n_keys)
    base = [f"node-{i}" for i in range(n_nodes)]

    ring = HashRing(vnodes=vnodes)
    for n in base:
        ring.add_node(n)
    before = ring.map_keys(keys)

    # 增加一个节点：理论下限 1/(n+1)
    ring.add_node(f"node-{n_nodes}")
    after = ring.map_keys(keys)
    moved_add = sum(1 for k in keys if before[k] != after[k])
    unrelated_add = sum(1 for k in keys
                        if before[k] != after[k] and after[k] != f"node-{n_nodes}")
    new_share = sum(1 for k in keys if after[k] == f"node-{n_nodes}") / len(keys)

    # 删除一个节点：理论下限 1/n
    ring2 = HashRing(vnodes=vnodes)
    for n in base:
        ring2.add_node(n)
    before2 = ring2.map_keys(keys)
    ring2.remove_node(base[-1])
    after2 = ring2.map_keys(keys)
    moved_del = sum(1 for k in keys if before2[k] != after2[k])
    unrelated_del = sum(1 for k in keys
                        if before2[k] != after2[k] and before2[k] != base[-1])

    print(f"[迁移] 节点数 {n_nodes}, 键数 {n_keys}, 虚拟节点/节点 {vnodes}")
    lo_add = 1 / (n_nodes + 1)
    lo_del = 1 / n_nodes
    print(f"  增节点: 迁移 {moved_add/len(keys)*100:6.2f}%  理论下限 {lo_add*100:5.2f}%  "
          f"新节点实际份额 {new_share*100:5.2f}%  无关迁移 {unrelated_add}")
    print(f"  删节点: 迁移 {moved_del/len(keys)*100:6.2f}%  理论下限 {lo_del*100:5.2f}%  "
          f"倍数 {moved_del/len(keys)/lo_del:.2f}x  无关迁移 {unrelated_del}")


def bench_distribution(n_nodes=10, n_keys=100_000):
    print(f"[分布] 节点数 {n_nodes}, 键数 {n_keys}（等权重）")
    print(f"  {'vnodes':>7} {'最大偏差':>9} {'标准差/均值':>11}")
    for vnodes in (10, 25, 50, 100, 160, 200, 400, 800, 1600):
        ring = HashRing(vnodes=vnodes)
        for i in range(n_nodes):
            ring.add_node(f"node-{i}")
        counts = {f"node-{i}": 0 for i in range(n_nodes)}
        for k in make_keys(n_keys):
            counts[ring.get_node(k)] += 1
        mean = n_keys / n_nodes
        max_dev = max(abs(c - mean) / mean for c in counts.values())
        cv = statistics.pstdev(counts.values()) / mean
        print(f"  {vnodes:>7} {max_dev*100:>8.1f}% {cv*100:>10.1f}%")


def bench_perf(n_keys=1_000_000, n_nodes=10, vnodes=160):
    ring = HashRing(vnodes=vnodes)
    for i in range(n_nodes):
        ring.add_node(f"node-{i}")
    keys = make_keys(n_keys)
    start = time.perf_counter()
    for k in keys:
        ring.get_node(k)
    elapsed = time.perf_counter() - start
    print(f"[性能] {n_keys:,} 次查询, 环规模 {ring.ring_size} 点: "
          f"总耗时 {elapsed:.2f}s, 单次 {elapsed/n_keys*1e6:.2f}µs, "
          f"{n_keys/elapsed/1e6:.2f}M QPS")


if __name__ == "__main__":
    bench_migration()
    print()
    bench_distribution()
    print()
    bench_perf()
