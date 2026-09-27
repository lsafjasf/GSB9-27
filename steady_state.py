"""稳态（平稳）分布计算库 —— 仅使用 Python 标准库。

提供两条求解路径：
1. power_iteration   : 幂迭代（可选 Cesàro 平均以处理周期链）
2. stationary_linear : 线性方程组直接求解（高斯消元 + 部分主元）

并对不收敛/非唯一情形（周期链、可约链）给出判定与说明。
"""

from __future__ import annotations

import math
from collections import deque

__all__ = [
    "MarkovChainError",
    "validate_transition_matrix",
    "stationarity_residual",
    "assert_stationary_properties",
    "power_iteration",
    "stationary_linear",
    "closed_communicating_classes",
    "chain_period",
    "analyze_chain",
]


class MarkovChainError(ValueError):
    """转移矩阵非法或稳态分布不唯一时抛出。"""


# ---------------------------------------------------------------------------
# 校验与固有性质
# ---------------------------------------------------------------------------

def validate_transition_matrix(P, tol=1e-9):
    """校验 P 是行随机矩阵：方阵、元素非负、每行和为 1。否则抛 MarkovChainError。"""
    if not isinstance(P, (list, tuple)) or len(P) == 0:
        raise MarkovChainError("转移矩阵必须是非空二维列表")
    n = len(P)
    for i, row in enumerate(P):
        if not isinstance(row, (list, tuple)) or len(row) != n:
            raise MarkovChainError(f"转移矩阵必须是 {n}x{n} 方阵，第 {i} 行长度非法")
        s = 0.0
        for j, x in enumerate(row):
            if not isinstance(x, (int, float)) or isinstance(x, bool) or math.isnan(x) or math.isinf(x):
                raise MarkovChainError(f"P[{i}][{j}]={x!r} 不是有限实数")
            if x < -tol:
                raise MarkovChainError(f"P[{i}][{j}]={x} 为负，不是合法概率")
            s += x
        if abs(s - 1.0) > tol:
            raise MarkovChainError(
                f"概率矩阵行和不为 1：第 {i} 行和 = {s!r}（容差 {tol}）"
            )
    return n


def _mat_vec(pi, P):
    """pi @ P（行向量右乘转移矩阵）。"""
    n = len(P)
    out = [0.0] * n
    for j in range(n):
        acc = 0.0
        for i in range(n):
            acc += pi[i] * P[i][j]
        out[j] = acc
    return out


def stationarity_residual(P, pi):
    """平稳方程残差 ||pi P - pi||_1。"""
    nxt = _mat_vec(pi, P)
    return sum(abs(a - b) for a, b in zip(nxt, pi))


def assert_stationary_properties(P, pi, tol=1e-8):
    """断言稳态分布的固有性质：非负、和为 1、满足平稳方程。返回残差。"""
    n = len(P)
    assert len(pi) == n, f"分布维度 {len(pi)} 与矩阵阶数 {n} 不一致"
    for i, x in enumerate(pi):
        assert x >= -tol, f"pi[{i}]={x} 为负，违反非负性"
    total = sum(pi)
    assert abs(total - 1.0) <= tol, f"分布和为 {total}，不等于 1"
    res = stationarity_residual(P, pi)
    assert res <= tol, f"平稳方程残差 {res:.3e} 超过容差 {tol:.1e}"
    return res


# ---------------------------------------------------------------------------
# 链结构分析：闭互通类（可约性）与周期
# ---------------------------------------------------------------------------

def _scc(P):
    """Tarjan 强连通分量（迭代实现），返回分量列表（每个分量是状态列表）。"""
    n = len(P)
    index_of = [-1] * n
    lowlink = [0] * n
    on_stack = [False] * n
    stack = []
    comps = []
    counter = [0]

    for start in range(n):
        if index_of[start] != -1:
            continue
        work = [(start, 0)]
        while work:
            v, ci = work[-1]
            if ci == 0:
                index_of[v] = lowlink[v] = counter[0]
                counter[0] += 1
                stack.append(v)
                on_stack[v] = True
            recurse = False
            neighbors = [w for w in range(n) if P[v][w] > 0]
            for k in range(ci, len(neighbors)):
                w = neighbors[k]
                if index_of[w] == -1:
                    work[-1] = (v, k + 1)
                    work.append((w, 0))
                    recurse = True
                    break
                elif on_stack[w]:
                    lowlink[v] = min(lowlink[v], index_of[w])
            if recurse:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[v])
            if lowlink[v] == index_of[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                comps.append(sorted(comp))
    return comps


def closed_communicating_classes(P):
    """返回所有闭互通类（没有其他类出边的 SCC）。多个闭类 => 稳态分布不唯一。"""
    n = len(P)
    comps = _scc(P)
    comp_id = {}
    for cid, comp in enumerate(comps):
        for v in comp:
            comp_id[v] = cid
    closed = []
    for cid, comp in enumerate(comps):
        is_closed = True
        for v in comp:
            for w in range(n):
                if P[v][w] > 0 and comp_id[w] != cid:
                    is_closed = False
                    break
            if not is_closed:
                break
        if is_closed:
            closed.append(comp)
    return closed


def chain_period(P, states=None):
    """计算（不可约子）链的周期：从某状态出发返回步长的 gcd。

    states 缺省为全部状态；若子图不连通返回 None。
    """
    n = len(P)
    if states is None:
        states = list(range(n))
    state_set = set(states)
    start = states[0]
    level = {start: 0}
    g = 0
    dq = deque([start])
    while dq:
        u = dq.popleft()
        for v in range(n):
            if P[u][v] > 0 and v in state_set:
                if v not in level:
                    level[v] = level[u] + 1
                    dq.append(v)
                else:
                    g = math.gcd(g, level[u] + 1 - level[v])
    if len(level) < len(state_set):
        return None
    return max(g, 1)


def analyze_chain(P):
    """汇总链的结构信息：闭类个数、各闭类周期、是否不可约/非周期。"""
    n = validate_transition_matrix(P)
    classes = closed_communicating_classes(P)
    periods = [chain_period(P, comp) for comp in classes]
    return {
        "n_states": n,
        "n_closed_classes": len(classes),
        "closed_classes": classes,
        "periods": periods,
        "irreducible": len(classes) == 1 and len(classes[0]) == n,
        "max_period": max(periods) if periods else 1,
    }


def _diagnose(P):
    """根据链结构生成人类可读的诊断说明。"""
    info = analyze_chain(P)
    msgs = []
    if info["n_closed_classes"] > 1:
        msgs.append(
            f"可约链：存在 {info['n_closed_classes']} 个闭互通类 "
            f"{info['closed_classes']}，稳态分布不唯一，"
            "极限分布依赖初始分布在各闭类上的质量；"
            "线性方程组因系数矩阵奇异而无唯一解。"
        )
    for comp, d in zip(info["closed_classes"], info["periods"]):
        if d and d > 1:
            msgs.append(
                f"周期链：闭类 {comp} 的周期 d={d}，"
                "幂迭代将在 d 个极限分布之间循环振荡而不收敛；"
                "可改用 Cesàro 平均（cesaro=True）或线性方程组路径。"
            )
    if not msgs:
        msgs.append(
            "链不可约且非周期，理论上幂迭代必收敛；"
            "未收敛说明混合时间过长，请增大 max_iter 或放宽 tol。"
        )
    return " ".join(msgs)


# ---------------------------------------------------------------------------
# 路径一：幂迭代
# ---------------------------------------------------------------------------

def power_iteration(P, tol=1e-12, max_iter=100_000, cesaro=False, record_every=1, pi0=None):
    """幂迭代求稳态分布。

    返回 dict：
      converged  : 是否收敛
      pi         : 分布（未收敛时为最后一次迭代值 / Cesàro 平均）
      iterations : 实际迭代次数
      residual   : 最终平稳方程残差 ||pi P - pi||_1
      history    : [(iter, residual), ...] 残差下降记录
      diagnosis  : 未收敛时的判定与说明（收敛时为 None）
      pi0        : 初始分布（缺省为均匀分布）
    """
    n = validate_transition_matrix(P)
    if pi0 is None:
        pi = [1.0 / n] * n
    else:
        if len(pi0) != n or any(x < 0 for x in pi0) or abs(sum(pi0) - 1.0) > 1e-9:
            raise MarkovChainError("初始分布 pi0 必须非负、和为 1 且维度匹配")
        pi = list(pi0)
    avg = [0.0] * n
    history = []

    for k in range(1, max_iter + 1):
        pi = _mat_vec(pi, P)
        if cesaro:
            for i in range(n):
                avg[i] += (pi[i] - avg[i]) / k
            cand = avg
        else:
            cand = pi
        res = stationarity_residual(P, cand)
        if k % record_every == 0 or res < tol:
            history.append((k, res))
        if res < tol:
            return {
                "converged": True,
                "pi": cand,
                "iterations": k,
                "residual": res,
                "history": history,
                "diagnosis": None,
            }

    final = avg if cesaro else pi
    return {
        "converged": False,
        "pi": final,
        "iterations": max_iter,
        "residual": stationarity_residual(P, final),
        "history": history,
        "diagnosis": _diagnose(P),
    }


# ---------------------------------------------------------------------------
# 路径二：线性方程组直接求解
# ---------------------------------------------------------------------------

def stationary_linear(P, tol=1e-12):
    """解 (P^T - I) pi = 0 且 sum(pi) = 1（高斯消元 + 部分主元）。

    对周期链同样有效（平稳方程不依赖周期性）。
    若链可约且含多个闭类，系数矩阵奇异，抛 MarkovChainError。
    """
    n = validate_transition_matrix(P)
    A = [[P[j][i] - (1.0 if i == j else 0.0) for j in range(n)] for i in range(n)]
    b = [0.0] * n
    A[n - 1] = [1.0] * n
    b[n - 1] = 1.0

    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(A[r][col]))
        if abs(A[piv][col]) < tol:
            raise MarkovChainError(
                "系数矩阵奇异：链可约且含多个闭互通类，稳态分布不唯一。"
                + " " + _diagnose(P)
            )
        if piv != col:
            A[col], A[piv] = A[piv], A[col]
            b[col], b[piv] = b[piv], b[col]
        for r in range(col + 1, n):
            f = A[r][col] / A[col][col]
            if f == 0.0:
                continue
            for c in range(col, n):
                A[r][c] -= f * A[col][c]
            b[r] -= f * b[col]

    pi = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = b[i] - sum(A[i][j] * pi[j] for j in range(i + 1, n))
        pi[i] = s / A[i][i]

    pi = [x if x > 0 else 0.0 for x in pi]
    total = sum(pi)
    if total <= 0:
        raise MarkovChainError("求解失败：得到零向量")
    pi = [x / total for x in pi]
    return pi
