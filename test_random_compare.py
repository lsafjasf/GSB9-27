"""随机小图对拍：Tarjan 结果必须与暴力可达性结果完全一致。

同时校验：
  1. 同一分量内任意两点互相可达（用暴力可达集验证）；
  2. 压缩图无环（DAG 性质）；
  3. 压缩图边与原图边一致（投影校验）。
"""
import random
from collections import deque

from scc import tarjan_scc, condensation, canonical
from brute_force import solve_brute, reachable_sets


def check_invariants(nodes, edges, comp, dag):
    reach = reachable_sets(nodes, edges)
    groups = {}
    for v, c in comp.items():
        groups.setdefault(c, []).append(v)
    # 1. 同分量互相可达；不同分量不互相可达
    for members in groups.values():
        for a in members:
            for b in members:
                assert b in reach[a], f"同分量不可达: {a}->{b}"
    for c1, m1 in groups.items():
        for c2, m2 in groups.items():
            if c1 >= c2:
                continue
            for a in m1:
                for b in m2:
                    assert not (b in reach[a] and a in reach[b]), \
                        f"不同分量却互相可达: {a},{b}"
    # 2. 压缩图无环（拓扑排序可完成）
    indeg = [0] * len(dag)
    for c, outs in enumerate(dag):
        for d in outs:
            assert d != c, "压缩图存在自环"
            indeg[d] += 1
    dq = deque([c for c in range(len(dag)) if indeg[c] == 0])
    seen = 0
    while dq:
        u = dq.popleft()
        seen += 1
        for w in dag[u]:
            indeg[w] -= 1
            if indeg[w] == 0:
                dq.append(w)
    assert seen == len(dag), "压缩图存在环"
    # 3. 每条原图边要么落在分量内，要么出现在压缩图中
    for u, v in edges:
        cu, cv = comp[u], comp[v]
        assert cu == cv or cv in dag[cu], f"边 {(u, v)} 未体现在压缩图"


def random_graph(rng, n, m):
    nodes = list(range(n))
    edges = [(rng.randrange(n), rng.randrange(n)) for _ in range(m)]
    return nodes, edges


def main():
    rng = random.Random(20260927)
    cases = 0
    for trial in range(600):
        n = rng.randint(1, 14)
        m = rng.randint(0, 3 * n + 4)
        nodes, edges = random_graph(rng, n, m)
        comp, cnt = tarjan_scc(nodes, edges)
        dag = condensation(edges, comp)
        got = canonical(comp, dag)
        want = solve_brute(nodes, edges)
        assert got == want, f"对拍失败 trial={trial} n={n} m={m}\n{got}\n{want}"
        check_invariants(nodes, edges, comp, dag)
        cases += 1
    print(f"OK: {cases} 个随机小图与暴力可达性对拍一致，不变量全部通过")


if __name__ == "__main__":
    main()
