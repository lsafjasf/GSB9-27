"""pagerank 库的自测、对拍与压测脚本。运行: python3 test_pagerank.py"""

import random
import sys
import time

from pagerank import pagerank_iterative, pagerank_direct

TOL_CMP = 1e-9   # 迭代法 vs 直接解的分差容差
TOL_SUM = 1e-9   # 守恒容差

results = []  # (用例, 节点数, 边数, 迭代轮数, 最终残差, 分数和, 对拍最大分差)


def check_conservation(name, scores):
    total = sum(scores.values())
    assert abs(total - 1.0) < TOL_SUM, f"{name}: 分数和失守恒 sum={total!r}"
    return total


def compare_with_direct(name, graph, **kw):
    """同一小图上跑迭代法与直接解，断言分差在容差内。"""
    kw.setdefault("max_iter", 10000)  # 对拍需充分收敛
    scores_it, iters, residual = pagerank_iterative(
        graph, check_conservation=True, **kw)
    scores_dir = pagerank_direct(
        graph, **{k: v for k, v in kw.items()
                  if k in ("damping", "teleport", "dangling")})
    total = check_conservation(name, scores_it)
    check_conservation(name + "(direct)", scores_dir)
    max_diff = max((abs(scores_it[u] - scores_dir[u]) for u in scores_it),
                   default=0.0)
    assert max_diff < TOL_CMP, f"{name}: 对拍分差 {max_diff:.3e} 超容差"
    m = sum(len(v) for v in graph.values())
    results.append((name, len(scores_it), m, iters, residual, total, max_diff))
    return scores_it


def main():
    # 1. 空图
    s, iters, res = pagerank_iterative({})
    assert s == {} and iters == 0 and res == 0.0
    assert pagerank_direct({}) == {}
    results.append(("空图", 0, 0, 0, 0.0, 0.0, 0.0))

    # 2. 单点（无出边，即悬挂节点）
    s = compare_with_direct("单点(悬挂)", {"a": []})
    assert abs(s["a"] - 1.0) < TOL_SUM

    # 3. 单点带自环
    s = compare_with_direct("单点(自环)", {"a": ["a"]})
    assert abs(s["a"] - 1.0) < TOL_SUM

    # 4. 完全图 K8（无自环）：对称性 => 分数均匀
    n = 8
    complete = {i: [j for j in range(n) if j != i] for i in range(n)}
    s = compare_with_direct("完全图K8", complete)
    for v in s:
        assert abs(s[v] - 1.0 / n) < TOL_CMP, "完全图分数应均匀"

    # 5. 含悬挂节点的链: 0->1->2, 2 悬挂；另有 1->3, 3 悬挂
    chain = {0: [1], 1: [2, 3], 2: [], 3: []}
    compare_with_direct("悬挂节点链", chain)

    # 6. 多个互不相通的子图：三角形环 + 二元环 + 孤立点
    disconnected = {0: [1], 1: [2], 2: [0], 3: [4], 4: [3], 5: []}
    compare_with_direct("多不连通分量", disconnected)

    # 7. 不同阻尼因子与悬挂分配规则
    compare_with_direct("阻尼0.5", disconnected, damping=0.5)
    compare_with_direct("阻尼0.99", chain, damping=0.99)
    compare_with_direct("悬挂均匀分配", chain, dangling="uniform")
    compare_with_direct("自定义teleport", chain,
                        teleport=[4.0, 1.0, 1.0, 1.0])

    # 8. 随机小图对拍（含随机悬挂节点与多分量），30 组种子
    for seed in range(30):
        rng = random.Random(seed)
        nn = rng.randint(2, 12)
        g = {i: [] for i in range(nn)}
        for u in range(nn):
            for v in range(nn):
                if u != v and rng.random() < 0.25:
                    g[u].append(v)
        compare_with_direct(f"随机图seed={seed}", g, damping=0.85)

    # 9. 守恒断言：每一轮迭代都检查（check_conservation=True 已在上面覆盖）
    #    这里再显式验证一次最终分数和
    s, _, _ = pagerank_iterative(disconnected)
    assert abs(sum(s.values()) - 1.0) < TOL_SUM

    # 10. 未收敛路径：极小 tol 触发 max_iter 上限
    _, iters, res = pagerank_iterative(chain, tol=0.0, max_iter=7)
    assert iters == 7 and res > 0.0, "应达到轮数上限且未收敛"
    results.append(("上限轮数(max_iter=7)", 4, 3, iters, res, 1.0, "-"))

    print("=" * 88)
    print("对拍与收敛数据（迭代法 vs 线性方程组直接解）")
    print("=" * 88)
    hdr = ("用例", "节点", "边", "轮数", "最终残差", "分数和", "对拍最大分差")
    print(f"{hdr[0]:<22}{hdr[1]:>5}{hdr[2]:>6}{hdr[3]:>6}"
          f"{hdr[4]:>12}{hdr[5]:>18}{hdr[6]:>14}")
    for name, nn, m, it, res, total, diff in results:
        diff_s = f"{diff:.2e}" if isinstance(diff, float) else diff
        total_s = f"{total:.15f}" if isinstance(total, float) else total
        print(f"{name:<22}{nn:>5}{m:>6}{it:>6}"
              f"{res:>12.3e}{total_s:>18}{diff_s:>14}")

    # 11. 百万边压测
    print()
    print("=" * 88)
    print("百万边压测（随机有向图，含悬挂节点与多分量）")
    print("=" * 88)
    n_big, m_big = 100_000, 1_000_000
    rng = random.Random(42)
    big = {i: [] for i in range(n_big)}
    for _ in range(m_big):
        u = rng.randrange(n_big)
        v = rng.randrange(n_big)
        if u != v:
            big[u].append(v)
    t0 = time.perf_counter()
    s_big, iters_big, res_big = pagerank_iterative(
        big, damping=0.85, tol=1e-10, max_iter=200)
    t1 = time.perf_counter()
    total_big = sum(s_big.values())
    assert abs(total_big - 1.0) < 1e-8, f"压测分数和失守恒: {total_big!r}"
    print(f"节点数        : {n_big:,}")
    print(f"边数          : {m_big:,}")
    print(f"收敛轮数      : {iters_big}")
    print(f"最终残差      : {res_big:.3e}")
    print(f"分数和        : {total_big:.15f}")
    print(f"总耗时        : {t1 - t0:.2f} s")
    print(f"每轮平均耗时  : {(t1 - t0) / iters_big * 1000:.1f} ms")
    print()
    print("全部断言通过 ✔")


if __name__ == "__main__":
    sys.exit(main())
