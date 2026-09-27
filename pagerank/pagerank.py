"""迭代排序（PageRank 风格）库 —— 仅使用 Python 标准库。

模型
----
有向图 G = (V, E)，N = |V|。节点 i 的出度记为 outdeg(i)。

迭代公式（可配置阻尼因子 d）：

    r_new[i] = (1 - d) / N
             + d * ( sum_{j->i} r[j] / outdeg(j)        # 正常出边贡献
                   + dangling_mass / N )                # 悬挂节点贡献

其中 dangling_mass = sum_{outdeg(j) == 0} r[j]。

悬挂节点分数分配规则
--------------------
没有出边的节点（悬挂节点）的分数被**均匀分配**给图中全部 N 个节点
（等价于给悬挂节点补上指向所有节点的 N 条出边，含自环）。
这样每一轮迭代中，每个节点的分数都被完整地转移出去：
  - 非悬挂节点 j：r[j] 被均分给 outdeg(j) 个后继；
  - 悬挂节点 j：r[j] 被均分给全部 N 个节点。
再叠加每轮均匀注入的 (1-d) 传送项，总和为 (1-d)。
因此若初始向量满足 sum(r) = 1，则每轮迭代后仍有 sum(r_new) = 1
（浮点误差范围内），即分数总和守恒。库中提供 assert_conservation()
做显式守恒断言。

收敛判据
--------
残差定义为两次迭代之间的**最大变化量**（L-inf 范数）：

    residual = max_i |r_new[i] - r_old[i]|

当 residual < tol 时判定收敛，同时设置最大轮数 max_iter 兜底。
返回收敛轮数与最终残差。
"""

from typing import Dict, Hashable, Iterable, List, Tuple

__all__ = [
    "iterative_rank",
    "direct_rank",
    "assert_conservation",
    "RankResult",
]

# 边列表类型: [(src, dst), ...]，节点可为任意可哈希对象
Edge = Tuple[Hashable, Hashable]


class RankResult:
    """迭代排序结果。"""

    __slots__ = ("scores", "iterations", "residual", "converged")

    def __init__(self, scores: Dict, iterations: int, residual: float, converged: bool):
        self.scores = scores          # {node: score}
        self.iterations = iterations  # 实际执行的迭代轮数
        self.residual = residual      # 最终残差（最后一轮的最大变化量）
        self.converged = converged    # 是否在 max_iter 内收敛

    def __repr__(self):
        return (f"RankResult(iterations={self.iterations}, "
                f"residual={self.residual:.3e}, converged={self.converged})")


def _index_graph(nodes: List, edges: Iterable[Edge]):
    """把任意节点标号映射到 0..N-1，并构建出边邻接表与出度数组。"""
    idx = {v: i for i, v in enumerate(nodes)}
    n = len(nodes)
    out_edges: List[List[int]] = [[] for _ in range(n)]
    for src, dst in edges:
        if src not in idx or dst not in idx:
            raise ValueError(f"边 ({src!r}, {dst!r}) 含有未声明的节点")
        out_edges[idx[src]].append(idx[dst])
    out_deg = [len(adj) for adj in out_edges]
    return idx, out_edges, out_deg


def iterative_rank(nodes: Iterable[Hashable],
                   edges: Iterable[Edge],
                   damping: float = 0.85,
                   tol: float = 1e-12,
                   max_iter: int = 1000) -> RankResult:
    """迭代法计算节点排序分数。

    参数
    ----
    nodes    : 节点集合（显式给出，保证孤立点也参与排序）
    edges    : 有向边列表 [(src, dst), ...]，允许重边（按多重贡献计）
    damping  : 阻尼因子 d ∈ [0, 1)
    tol      : 收敛阈值，两轮迭代分数向量的最大变化量小于 tol 即收敛
    max_iter : 最大迭代轮数上限

    返回 RankResult，含分数字典、收敛轮数、最终残差、是否收敛。
    """
    if not 0.0 <= damping < 1.0:
        raise ValueError("damping 必须满足 0 <= d < 1")
    nodes = list(nodes)
    n = len(nodes)
    if n == 0:
        return RankResult({}, 0, 0.0, True)

    idx, out_edges, out_deg = _index_graph(nodes, edges)

    # 初始化为均匀分布，sum(r) = 1
    r = [1.0 / n] * n
    teleport = (1.0 - damping) / n

    iterations = 0
    residual = float("inf")
    converged = False

    for it in range(1, max_iter + 1):
        # 悬挂节点总质量，均匀分给全部节点
        dangling_mass = 0.0
        for j in range(n):
            if out_deg[j] == 0:
                dangling_mass += r[j]
        base = teleport + damping * dangling_mass / n

        new_r = [base] * n
        for j in range(n):
            deg = out_deg[j]
            if deg == 0:
                continue
            share = damping * r[j] / deg
            for dst in out_edges[j]:
                new_r[dst] += share

        # 残差：两轮之间的最大变化量
        residual = max(abs(new_r[i] - r[i]) for i in range(n))
        r = new_r
        iterations = it
        if residual < tol:
            converged = True
            break

    scores = {v: r[idx[v]] for v in nodes}
    return RankResult(scores, iterations, residual, converged)


def direct_rank(nodes: Iterable[Hashable],
                edges: Iterable[Edge],
                damping: float = 0.85) -> Dict:
    """直接法：解线性方程组 (I - d*L) r = (1-d)/N * 1。

    L 为列随机转移矩阵（悬挂节点列补为全 1/N），与迭代公式严格对应：
        r = (1-d)/N * 1 + d * L r
    用部分主元高斯消元求解，供小规模图与迭代法对拍。
    """
    if not 0.0 <= damping < 1.0:
        raise ValueError("damping 必须满足 0 <= d < 1")
    nodes = list(nodes)
    n = len(nodes)
    if n == 0:
        return {}

    idx, out_edges, out_deg = _index_graph(nodes, edges)

    # 组装增广矩阵 A r = b，A = I - d*L，b = (1-d)/N
    a = [[0.0] * (n + 1) for _ in range(n)]
    for i in range(n):
        a[i][i] = 1.0
        a[i][n] = (1.0 - damping) / n
    for j in range(n):
        deg = out_deg[j]
        if deg == 0:
            # 悬挂节点：列补为全 1/N（均匀分配给所有节点）
            for i in range(n):
                a[i][j] -= damping / n
        else:
            w = damping / deg
            for dst in out_edges[j]:
                a[dst][j] -= w

    # 部分主元高斯消元
    for col in range(n):
        piv = max(range(col, n), key=lambda r_: abs(a[r_][col]))
        if abs(a[piv][col]) < 1e-15:
            raise ArithmeticError(f"矩阵奇异（第 {col} 列）")
        a[col], a[piv] = a[piv], a[col]
        inv = 1.0 / a[col][col]
        for r_ in range(col + 1, n):
            factor = a[r_][col] * inv
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                a[r_][c] -= factor * a[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = a[i][n] - sum(a[i][j] * x[j] for j in range(i + 1, n))
        x[i] = s / a[i][i]

    return {v: x[idx[v]] for v in nodes}


def assert_conservation(scores: Dict, expected: float = 1.0,
                        tol: float = 1e-9) -> float:
    """守恒断言：所有分数之和应等于 expected（默认 1.0）。

    返回实际总和；超出容差则抛 AssertionError。
    """
    total = sum(scores.values())
    if abs(total - expected) > tol:
        raise AssertionError(
            f"分数总和 {total!r} 偏离 {expected} 超过容差 {tol}")
    return total
