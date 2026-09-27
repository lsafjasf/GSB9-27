"""PageRank 风格的关系图节点迭代排序库（仅标准库）。

模型与记号
----------
图 G = (V, E)，n = |V|。每个节点 v 的分数 r[v] 表示其重要性，
所有分数之和恒等于 1（守恒）。

迭代公式（可配置）：
    r_new[v] = (1 - d) * t[v]
             + d * ( sum_{u->v} r[u] / outdeg(u)        # 正常出边贡献
                   + S_dang * w[v] )                    # 悬挂节点再分配
其中：
    d       阻尼因子 damping（默认 0.85，可配置）
    t[v]    传送向量 teleport（默认均匀 1/n，可配置；必须和为 1）
    S_dang  所有悬挂节点（无出边节点）的分数之和
    w[v]    悬挂分数的分配权重， dangling="uniform" 时为 1/n，
            dangling="teleport" 时等于 t[v]（默认，保证守恒）

悬挂节点规则（守恒证明）：
    悬挂节点没有出边，其分数若不处理会“漏掉”。本库把悬挂节点的全部
    分数 S_dang 按权重 w（和为 1）重新分配给全图节点，因此
    sum(r_new) = (1-d)*1 + d*( sum_{u 非悬挂} r[u] + S_dang )
               = (1-d) + d*1 = 1。每轮迭代严格守恒（浮点误差内）。

收敛判据：
    residual = max_v |r_new[v] - r[v]| （两次迭代间的最大变化量）
    residual <= tol 时判定收敛；迭代轮数不超过 max_iter。
    返回值包含实际迭代轮数与最终残差。

直接解（对拍用）：
    定义列归一化转移矩阵 B[v][u] = 1/outdeg(u)（若 u->v），
    对悬挂列 u 令 B[v][u] = w[v]。则不动点满足
        (I - d*B) r = (1-d) * t
    用部分主元高斯消元直接求解，仅适合小规模图（O(n^3)）。
"""

__all__ = ["pagerank_iterative", "pagerank_direct", "normalize_teleport"]


def _index_graph(graph):
    """把 {node: [out_neighbors]} 邻接表转成整数索引表示。

    返回 (nodes, adj, outdeg, dangling)：
      nodes    索引 -> 原节点标签
      adj      索引 -> 出边邻居索引列表
      outdeg   索引 -> 出度
      dangling 悬挂节点索引集合
    输入中只出现在邻居位置的节点也会被收录（出度为 0，即悬挂节点）。
    """
    nodes = []
    index = {}

    def idx(node):
        i = index.get(node)
        if i is None:
            i = len(nodes)
            index[node] = i
            nodes.append(node)
        return i

    raw = []
    for u, nbrs in graph.items():
        iu = idx(u)
        while len(raw) <= iu:
            raw.append([])
        raw[iu] = [idx(v) for v in nbrs]
    while len(raw) < len(nodes):
        raw.append([])

    adj = raw
    outdeg = [len(a) for a in adj]
    dangling = {i for i, d in enumerate(outdeg) if d == 0}
    return nodes, adj, outdeg, dangling


def normalize_teleport(n, teleport=None):
    """构造并校验传送向量：默认均匀分布；自定义时要求和为 1（自动归一化）。"""
    if teleport is None:
        return [1.0 / n] * n
    if len(teleport) != n:
        raise ValueError("teleport 长度必须等于节点数")
    s = float(sum(teleport))
    if s <= 0.0:
        raise ValueError("teleport 之和必须为正")
    return [float(x) / s for x in teleport]


def pagerank_iterative(graph, damping=0.85, tol=1e-12, max_iter=100,
                       teleport=None, dangling="teleport",
                       check_conservation=False):
    """迭代法排序。

    参数：
      graph     {node: [out_neighbors]} 邻接表（有向图）
      damping   阻尼因子 d ∈ [0, 1)
      tol       收敛阈值：两轮分数的最大变化量 <= tol 即收敛
      max_iter  最大迭代轮数上限
      teleport  传送向量（按 nodes 顺序或 None=均匀），自动归一化
      dangling  悬挂节点分数分配规则："teleport"(按 t) 或 "uniform"(均匀)
      check_conservation  若为 True，每轮断言分数和守恒（调试用）

    返回 (scores, iterations, residual)：
      scores      {node: score}，sum(scores.values()) == 1（浮点误差内）
      iterations  实际迭代轮数
      residual    最终残差（最后一轮的最大变化量；未收敛时 > tol）
    """
    if not 0.0 <= damping < 1.0:
        raise ValueError("damping 必须在 [0, 1) 内")
    if dangling not in ("teleport", "uniform"):
        raise ValueError("dangling 必须是 'teleport' 或 'uniform'")

    nodes, adj, outdeg, dang = _index_graph(graph)
    n = len(nodes)
    if n == 0:  # 空图
        return {}, 0, 0.0

    t = normalize_teleport(n, teleport)
    w = t if dangling == "teleport" else [1.0 / n] * n

    r = list(t)  # 初始化为传送向量，和为 1
    factor = [damping / d if d else 0.0 for d in outdeg]  # d/outdeg(u)
    dang_idx = sorted(dang)

    iterations = 0
    residual = float("inf")
    for it in range(1, max_iter + 1):
        s_dang = 0.0
        for u in dang_idx:
            s_dang += r[u]
        new = [(1.0 - damping) * t[v] + damping * s_dang * w[v]
               for v in range(n)]
        for u in range(n):
            fu = factor[u]
            if fu == 0.0:
                continue
            share = r[u] * fu
            for v in adj[u]:
                new[v] += share
        residual = 0.0
        for v in range(n):
            diff = new[v] - r[v]
            if diff < 0.0:
                diff = -diff
            if diff > residual:
                residual = diff
        r = new
        iterations = it
        if check_conservation:
            total = sum(r)
            assert abs(total - 1.0) < 1e-9, \
                f"第 {it} 轮分数和失守恒: {total!r}"
        if residual <= tol:
            break

    return {nodes[i]: r[i] for i in range(n)}, iterations, residual


def pagerank_direct(graph, damping=0.85, teleport=None, dangling="teleport"):
    """直接解线性方程组 (I - d*B) r = (1-d)*t，部分主元高斯消元。

    仅用于小规模图的对拍验证。返回 {node: score}。
    """
    if not 0.0 <= damping < 1.0:
        raise ValueError("damping 必须在 [0, 1) 内")
    if dangling not in ("teleport", "uniform"):
        raise ValueError("dangling 必须是 'teleport' 或 'uniform'")

    nodes, adj, outdeg, dang = _index_graph(graph)
    n = len(nodes)
    if n == 0:
        return {}

    t = normalize_teleport(n, teleport)
    w = t if dangling == "teleport" else [1.0 / n] * n

    # 构造系数矩阵 A = I - d*B 与右端 b = (1-d)*t
    a = [[0.0] * n for _ in range(n)]
    for u in range(n):
        if outdeg[u] == 0:
            for v in range(n):
                a[v][u] = -damping * w[v]
        else:
            inv = damping / outdeg[u]
            for v in adj[u]:
                a[v][u] -= inv
    for v in range(n):
        a[v][v] += 1.0
    b = [(1.0 - damping) * t[v] for v in range(n)]

    # 部分主元高斯消元
    for col in range(n):
        piv = max(range(col, n), key=lambda i: abs(a[i][col]))
        if abs(a[piv][col]) < 1e-300:
            raise ArithmeticError("矩阵奇异，无法直接求解")
        if piv != col:
            a[col], a[piv] = a[piv], a[col]
            b[col], b[piv] = b[piv], b[col]
        inv_piv = 1.0 / a[col][col]
        for row in range(col + 1, n):
            f = a[row][col] * inv_piv
            if f == 0.0:
                continue
            for k in range(col, n):
                a[row][k] -= f * a[col][k]
            b[row] -= f * b[col]
    r = [0.0] * n
    for col in range(n - 1, -1, -1):
        s = b[col]
        for k in range(col + 1, n):
            s -= a[col][k] * r[k]
        r[col] = s / a[col][col]

    return {nodes[i]: r[i] for i in range(n)}
