"""规模基准：十万点链式 / 稀疏 / 稠密图的耗时与内存。

内存用 resource.ru_maxrss（进程峰值 RSS）与 tracemalloc（Python 堆
在算法运行期间的净增峰值）双重度量。
"""
import random
import resource
import sys
import time
import tracemalloc

from scc import tarjan_scc, condensation, canonical


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def run_case(name, nodes, edges):
    print(f"[{name}] V={len(nodes)} E={len(edges)}")
    print(f"  构建输入后 RSS: {rss_mb():.1f} MB")
    tracemalloc.start()
    t0 = time.perf_counter()
    comp, cnt = tarjan_scc(nodes, edges)
    t1 = time.perf_counter()
    dag = condensation(edges, comp)
    t2 = time.perf_counter()
    components, dag_edges = canonical(comp, dag)
    t3 = time.perf_counter()
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"  分量数: {cnt}, 压缩图边数: {len(dag_edges)}")
    print(f"  Tarjan: {t1 - t0:.3f}s  压缩图: {t2 - t1:.3f}s  "
          f"规范化: {t3 - t2:.3f}s  合计: {t3 - t0:.3f}s")
    print(f"  算法期间 Python 堆峰值: {py_peak / 2**20:.1f} MB, "
          f"进程峰值 RSS: {rss_mb():.1f} MB")
    # 抽查：同分量点对在压缩图上编号一致（ trivially true ），
    # 主要确认结果结构完整
    assert len(comp) == len(nodes)
    return components, dag_edges


def main():
    n = 100_000
    print(f"Python {sys.version.split()[0]}, recursionlimit="
          f"{sys.getrecursionlimit()}（本实现不使用递归）\n")

    # 1. 链式图：递归实现会爆栈的典型用例
    chain_nodes = list(range(n))
    chain_edges = [(i, i + 1) for i in range(n - 1)]
    comps, _ = run_case("链式 100k", chain_nodes, chain_edges)
    assert len(comps) == n, "链式图应每个点自成分量"
    print()

    # 2. 稀疏随机图：平均出度 3
    rng = random.Random(42)
    sparse_edges = [(rng.randrange(n), rng.randrange(n))
                    for _ in range(3 * n)]
    run_case("稀疏随机 100k (E=3V)", chain_nodes, sparse_edges)
    print()

    # 3. 稠密随机图：平均出度 20（E=2,000,000）
    dense_edges = [(rng.randrange(n), rng.randrange(n))
                   for _ in range(20 * n)]
    run_case("稠密随机 100k (E=20V)", chain_nodes, dense_edges)
    print()

    # 4. 强连通大块：一个 100k 点的大环 + 随机弦，应只有 1 个分量
    big_scc_edges = ([(i, (i + 1) % n) for i in range(n)]
                     + [(rng.randrange(n), rng.randrange(n))
                        for _ in range(n)])
    comps, _ = run_case("单一大分量 100k", chain_nodes, big_scc_edges)
    assert len(comps) == 1, "大环应缩成 1 个分量"
    print("\n全部基准完成")


if __name__ == "__main__":
    main()
