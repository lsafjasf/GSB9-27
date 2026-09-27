"""迭代版 Tarjan 强连通分量 + 压缩图（凝聚图）构建。

仅依赖标准库，全程迭代实现，不消耗递归栈，
可处理十万级链式图。

API:
    tarjan_scc(nodes, edges)   -> (comp, comp_count)
    condensation(edges, comp)  -> dag (list[set[int]])
    canonical(comp, dag)       -> (components, dag_edges) 规范化结果，
                                  与输入顺序无关，可直接用 == 比较。
"""

__all__ = ["tarjan_scc", "condensation", "canonical", "solve"]


def _safe_sorted(items):
    """尽量按自然序排序；不可比较时退化为 repr 排序，保证确定性。"""
    items = list(items)
    try:
        return sorted(items)
    except TypeError:
        return sorted(items, key=repr)


def tarjan_scc(nodes, edges):
    """迭代 Tarjan。

    nodes: 可迭代点集（可哈希）；edges: 可迭代 (u, v) 有向边。
    返回 (comp, comp_count)：comp[v] 为 v 所属分量编号（0..comp_count-1）。
    邻接表与遍历起点均排序，遍历顺序确定，与输入顺序无关。
    """
    adj = {}
    for v in nodes:
        adj.setdefault(v, [])
    for u, v in edges:
        adj.setdefault(u, []).append(v)
        adj.setdefault(v, [])
    for v in adj:
        adj[v] = _safe_sorted(adj[v])

    index = {}      # v -> dfs 序号
    low = {}        # v -> lowlink
    on_stack = set()
    stack = []
    comp = {}
    counter = 0
    comp_count = 0

    for root in _safe_sorted(adj.keys()):
        if root in index:
            continue
        # 显式工作栈：元素为 [node, 下一个待处理邻居下标]
        work = [[root, 0]]
        while work:
            top = work[-1]
            v, ci = top[0], top[1]
            if ci == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack.add(v)
            neighbors = adj[v]
            descended = False
            while ci < len(neighbors):
                w = neighbors[ci]
                ci += 1
                if w not in index:
                    top[1] = ci
                    work.append([w, 0])
                    descended = True
                    break
                elif w in on_stack and index[w] < low[v]:
                    low[v] = index[w]
            if descended:
                continue
            top[1] = ci
            work.pop()
            if low[v] == index[v]:
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp[w] = comp_count
                    if w == v:
                        break
                comp_count += 1
            if work:
                parent = work[-1][0]
                if low[v] < low[parent]:
                    low[parent] = low[v]

    return comp, comp_count


def condensation(edges, comp):
    """由分量划分构建压缩图（DAG）。

    返回 dag: list[set]，dag[c] 为分量 c 指向的分量集合（不含自环）。
    """
    dag = [set() for _ in range(max(comp.values(), default=-1) + 1)]
    for u, v in edges:
        cu, cv = comp[u], comp[v]
        if cu != cv:
            dag[cu].add(cv)
    return dag


def canonical(comp, dag):
    """规范化结果，消除编号任意性，使结果可用 == 直接比较。

    返回 (components, dag_edges):
      components: tuple[tuple]，每个分量内点排序，分量间排序；
      dag_edges:  tuple[(i, j)]，规范编号下压缩图的有向边，排序去重。
    """
    groups = {}
    for v, c in comp.items():
        groups.setdefault(c, []).append(v)
    sorted_groups = [_safe_sorted(members) for members in groups.values()]
    order = sorted(range(len(sorted_groups)),
                   key=lambda c: [repr(x) for x in sorted_groups[c]])
    remap = {old: new for new, old in enumerate(order)}
    components = tuple(tuple(sorted_groups[old]) for old in order)
    dag_edges = tuple(sorted(
        {(remap[c], remap[d]) for c, outs in enumerate(dag) for d in outs}
    ))
    return components, dag_edges


def solve(nodes, edges):
    """一站式入口：返回规范化 (components, dag_edges)。"""
    nodes = list(nodes)
    edges = list(edges)
    comp, _ = tarjan_scc(nodes, edges)
    dag = condensation(edges, comp)
    return canonical(comp, dag)
