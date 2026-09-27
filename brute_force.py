"""暴力可达性参考实现（仅用于小规模对拍）。

对每个点 BFS 求可达集，互相可达的点划为同一分量；
分量间若存在可达关系则连边。O(V*(V+E))，仅适合小图。
"""
from collections import deque


def reachable_sets(nodes, edges):
    adj = {v: [] for v in nodes}
    for u, v in edges:
        adj[u].append(v)
    reach = {}
    for s in nodes:
        seen = {s}
        dq = deque([s])
        while dq:
            u = dq.popleft()
            for w in adj[u]:
                if w not in seen:
                    seen.add(w)
                    dq.append(w)
        reach[s] = seen
    return reach


def solve_brute(nodes, edges):
    """返回与 scc.canonical 同构的 (components, dag_edges)。"""
    nodes = list(nodes)
    edges = list(edges)
    reach = reachable_sets(nodes, edges)
    comp = {}
    groups = []
    for v in nodes:
        if v in comp:
            continue
        members = [u for u in nodes
                   if u in reach[v] and v in reach[u]]
        cid = len(groups)
        groups.append(members)
        for u in members:
            comp[u] = cid
    dag = [set() for _ in groups]
    for u, v in edges:
        cu, cv = comp[u], comp[v]
        if cu != cv:
            dag[cu].add(cv)
    from scc import canonical
    return canonical(comp, dag)
