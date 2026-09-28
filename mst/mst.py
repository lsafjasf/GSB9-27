"""最小生成森林（MSF）：并查集 Kruskal 与优先队列 Prim，纯标准库实现。

图的表示：
    n     : 顶点数，顶点编号为 0 .. n-1
    edges : 可迭代的 (u, v, w)，w 为数值权值（允许负权；拒绝 NaN）

等权边的确定性规则（Kruskal 与 Prim 共用同一把全局“尺子”）：
    1. 先按输入顺序给每条边分配唯一下标 idx（从 0 开始）；
    2. 边的严格全序为 (w, idx)：先比权值，权值相同则先出现的边优先；
    3. Kruskal 的排序、Prim 的堆顶比较都使用该全序。
    概念上等价于把权值扰动为 w + idx * epsilon（epsilon > 0 足够小，
    小到任何生成森林之间的秩和差都不足以反转一次真实权值差），扰动后
    生成树唯一，因此两种算法选出的边集合必然完全一致，而不只是总权值一致。

不连通图的森林语义：
    返回覆盖全部 n 个顶点的最小生成森林（每个连通分量一棵 MST）；
    components 为森林中树的棵数（孤立点也算一棵），所选边数为 n - components。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Sequence

Edge = tuple[int, int, int]  # (u, v, w)，输出时 u <= v


@dataclass(frozen=True)
class MSFResult:
    edges: list[Edge]          # 所选边，规范化为 (min(u,v), max(u,v), w)
    total_weight: int | float  # 总权值
    components: int            # 森林中树的棵数（孤立点算一棵）
    num_vertices: int          # 顶点总数 n


class _DSU:
    """带路径压缩与按大小合并的并查集。"""

    __slots__ = ("parent", "size")

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != x:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]
        return True


def _normalize(n: int, edges: Iterable[Sequence]) -> list[tuple[int, int, object, int]]:
    """校验并规范化为 (a, b, w, idx)，其中 a <= b，idx 为输入下标。"""

    normalized: list[tuple[int, int, object, int]] = []
    for idx, edge in enumerate(edges):
        if len(edge) != 3:
            raise ValueError(f"第 {idx} 条边格式应为 (u, v, w)")
        u, v, w = edge
        if not isinstance(u, int) or not isinstance(v, int) or isinstance(u, bool) or isinstance(v, bool):
            raise ValueError(f"第 {idx} 条边的顶点必须为整数")
        if not (0 <= u < n and 0 <= v < n):
            raise ValueError(f"第 {idx} 条边引用了越界顶点: ({u}, {v})，合法范围 [0, {n})")
        if w != w:  # NaN
            raise ValueError(f"第 {idx} 条边权值为 NaN")
        a, b = (u, v) if u <= v else (v, u)
        normalized.append((a, b, w, idx))
    return normalized


def kruskal_msf(n: int, edges: Iterable[Sequence]) -> MSFResult:
    """基于并查集的 Kruskal：按 (w, idx) 排序，依次加入不形成环的边。"""

    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise ValueError("顶点数 n 必须为非负整数")
    ordered = sorted(_normalize(n, edges), key=lambda e: (e[2], e[3]))
    dsu = _DSU(n)
    chosen: list[Edge] = []
    total = 0
    components = n
    for a, b, w, _idx in ordered:
        if a == b:  # 自环：加入必成环，直接跳过
            continue
        if dsu.union(a, b):
            chosen.append((a, b, w))
            total += w
            components -= 1
    chosen.sort(key=lambda e: (e[2], e[0], e[1]))
    return MSFResult(chosen, total, components, n)


def prim_msf(n: int, edges: Iterable[Sequence]) -> MSFResult:
    """基于优先队列的惰性 Prim：堆项按 (w, idx) 比较，逐棵生成 MST。"""

    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise ValueError("顶点数 n 必须为非负整数")
    normalized = _normalize(n, edges)
    adjacency: list[list[tuple[int, object, int]]] = [[] for _ in range(n)]
    for a, b, w, idx in normalized:
        if a == b:  # 自环永远不会成为割边，不入邻接表
            continue
        adjacency[a].append((b, w, idx))
        adjacency[b].append((a, w, idx))

    visited = bytearray(n)
    chosen: list[Edge] = []
    total = 0
    components = 0
    for root in range(n):  # 每个未访问的根开启一个新分量
        if visited[root]:
            continue
        components += 1
        visited[root] = 1
        heap: list[tuple[object, int, int, int]] = []
        for to, w, idx in adjacency[root]:
            heapq.heappush(heap, (w, idx, root, to))
        while heap:
            w, idx, frm, to = heapq.heappop(heap)
            if visited[to]:  # 过期边（两端都已在树中）
                continue
            visited[to] = 1
            a, b = (frm, to) if frm <= to else (to, frm)
            chosen.append((a, b, w))
            total += w
            for nxt, nw, nidx in adjacency[to]:
                if not visited[nxt]:
                    heapq.heappush(heap, (nw, nidx, to, nxt))
    chosen.sort(key=lambda e: (e[2], e[0], e[1]))
    return MSFResult(chosen, total, components, n)


def brute_force_msf(n: int, edges: Iterable[Sequence]) -> MSFResult:
    """小规模暴力最优：枚举所有边子集，用并查集判定是否为生成森林。

    目标按字典序最小化 (总权值, 秩和)，秩即输入下标；秩和最小对应
    Kruskal/Prim 在 (w, idx) 全序下选出的唯一解，便于逐条边对拍。
    仅适合很小的图（建议非自环边 <= 12）。
    """

    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise ValueError("顶点数 n 必须为非负整数")
    normalized = _normalize(n, edges)
    candidates = [e for e in normalized if e[0] != e[1]]

    dsu_all = _DSU(n)
    for a, b, _w, _idx in candidates:
        dsu_all.union(a, b)
    components = sum(1 for i in range(n) if dsu_all.find(i) == i)
    need = n - components  # 任何生成森林必须恰好包含 need 条边

    best_key = None
    best_edges: list[Edge] = []
    for combo in combinations(candidates, need):
        dsu = _DSU(n)
        for a, b, _w, _idx in combo:
            if not dsu.union(a, b):
                break  # 出现环：不是森林
        else:
            weight_sum = sum(e[2] for e in combo)
            rank_sum = sum(e[3] for e in combo)
            key = (weight_sum, rank_sum)
            if best_key is None or key < best_key:
                best_key = key
                best_edges = [(a, b, w) for a, b, w, _idx in combo]

    best_edges.sort(key=lambda e: (e[2], e[0], e[1]))
    total = best_key[0] if best_key is not None else 0
    return MSFResult(best_edges, total, components, n)
