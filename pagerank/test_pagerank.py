"""pagerank 库自测：特殊图覆盖 + 直接解对拍 + 守恒断言 + 百万边耗时。

运行: python3 test_pagerank.py
"""

import random
import sys
import time

from pagerank import iterative_rank, direct_rank, assert_conservation

COMPARE_TOL = 1e-8   # 迭代法 vs 直接法分差容差
DAMPING = 0.85

PASS = 0


def check(name, cond, detail=""):
    global PASS
    status = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    print(f"[{status}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        sys.exit(1)


def compare_with_direct(name, nodes, edges, damping=DAMPING):
    """小规模图：迭代法与线性方程组直接解对拍。"""
    res = iterative_rank(nodes, edges, damping=damping)
    ref = direct_rank(nodes, edges, damping=damping)
    total = assert_conservation(res.scores)          # 守恒断言
    assert_conservation(ref)
    max_diff = max((abs(res.scores[v] - ref[v]) for v in ref), default=0.0)
    check(f"{name} | 迭代 vs 直接解", max_diff < COMPARE_TOL,
          f"max_diff={max_diff:.2e}, iters={res.iterations}, "
          f"residual={res.residual:.2e}, sum={total:.15f}")


def main():
    print("== 特殊图覆盖 + 直接解对拍 ==")

    # 1. 空图
    res = iterative_rank([], [])
    check("空图", res.scores == {} and res.iterations == 0 and res.converged,
          f"scores={res.scores}")
    check("空图 | 直接解", direct_rank([], []) == {})

    # 2. 单点（无出边，是悬挂节点）
    compare_with_direct("单点", ["a"], [])
    res = iterative_rank(["a"], [])
    check("单点 | 分数为 1", abs(res.scores["a"] - 1.0) < 1e-12)

    # 3. 完全图（K6，含自环以外的所有有向边）
    k = 6
    nodes = list(range(k))
    edges = [(i, j) for i in nodes for j in nodes if i != j]
    compare_with_direct("完全图 K6", nodes, edges)
    res = iterative_rank(nodes, edges)
    check("完全图 | 分数均匀", all(abs(s - 1.0 / k) < 1e-9
                                   for s in res.scores.values()))

    # 4. 悬挂节点：0->1, 1->2, 2->1 （节点 0 无入边；再加一个无出边节点 3）
    #    0->1, 1->2, 2->1, 2->3；节点 3 为悬挂节点
    nodes = [0, 1, 2, 3]
    edges = [(0, 1), (1, 2), (2, 1), (2, 3)]
    compare_with_direct("悬挂节点", nodes, edges)

    # 5. 多个不连通分量：{0->1,1->0} 与 {2->3, 3->4, 4->2} 与孤立点 5
    nodes = [0, 1, 2, 3, 4, 5]
    edges = [(0, 1), (1, 0), (2, 3), (3, 4), (4, 2)]
    compare_with_direct("多不连通分量", nodes, edges)

    # 6. 随机小图对拍（多阻尼因子）
    rng = random.Random(42)
    for d in (0.5, 0.85, 0.99):
        n = 12
        nodes = list(range(n))
        edges = [(rng.randrange(n), rng.randrange(n)) for _ in range(30)]
        compare_with_direct(f"随机图 d={d}", nodes, edges, damping=d)

    # 7. 收敛行为：残差单调报告 + 上限轮数生效
    res = iterative_rank(nodes, edges, tol=1e-15, max_iter=5)
    check("上限轮数生效", res.iterations == 5 and not res.converged,
          f"iters={res.iterations}, residual={res.residual:.2e}")

    # 8. 守恒断言能抓错
    try:
        assert_conservation({"a": 0.3, "b": 0.3})
        caught = False
    except AssertionError:
        caught = True
    check("守恒断言可检出错误", caught)

    print()
    print("== 百万边耗时 ==")
    n, m = 100_000, 1_000_000
    rng = random.Random(7)
    big_nodes = list(range(n))
    big_edges = [(rng.randrange(n), rng.randrange(n)) for _ in range(m)]
    t0 = time.perf_counter()
    big = iterative_rank(big_nodes, big_edges, damping=0.85, tol=1e-10)
    dt = time.perf_counter() - t0
    total = assert_conservation(big.scores, tol=1e-6)
    check("百万边迭代", big.converged,
          f"N={n}, M={m}, iters={big.iterations}, "
          f"residual={big.residual:.2e}, sum={total:.12f}, "
          f"耗时={dt:.2f}s ({dt / big.iterations * 1000:.0f}ms/轮)")

    print()
    print(f"全部 {PASS} 项检查通过。")


if __name__ == "__main__":
    main()
