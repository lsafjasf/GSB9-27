"""有限状态离散时间马尔可夫链的稳态分布计算（仅依赖 Python 标准库）。

约定
----
转移矩阵 ``P`` 按行随机（row-stochastic）组织：``P[i][j]`` 表示从状态
``i`` 一步转移到状态 ``j`` 的概率，合法链要求每个元素非负且每行之和为 1。
平稳分布 ``pi`` 是行向量，满足 ``pi P = pi``、``sum(pi)=1``、``pi >= 0``。

两条求解路径
------------
* 幂迭代（power iteration）：``x_{k+1} = x_k P``，适用于非周期、单一闭类
  （遍历 / unichain-非周期）的链；周期链上 x_k 会振荡不收敛。
* 线性方程组：求解 ``(P^T - I) pi^T = 0`` 并用 ``sum(pi)=1`` 替换一个冗余
  方程，高斯-若尔当消元。若系数矩阵秩亏缺且存在多个闭类，则平稳分布不唯一
  （吸收链 / 可约多闭类）。

结构判定（Tarjan SCC + BFS 周期）与数值方法相互独立，可对“不收敛”给出
确定性的原因说明，而不依赖迭代是否碰巧稳定。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple


class InvalidChainError(ValueError):
    """转移矩阵不合法：非方阵、元素为负 / 非数值 / 非有限，或行和不为 1。"""


class NonConvergenceError(RuntimeError):
    """幂迭代在给定迭代预算内不收敛（典型原因：周期链或收敛停滞）。"""


class NonUniqueStationaryError(RuntimeError):
    """链存在多个闭连通类，平稳分布不唯一（如多吸收态的吸收链）。"""


@dataclass(frozen=True)
class Structure:
    """链的结构性分析结果。"""

    n: int
    sccs: List[List[int]]
    closed_classes: List[List[int]]
    irreducible: bool
    unichain: bool
    periods: List[int]
    aperiodic: bool
    ergodic_for_power: bool
    unique_stationary: bool
    reason: str


@dataclass
class PowerResult:
    """幂迭代结果。"""

    pi: List[float]
    converged: bool
    iterations: int
    residual: float
    history: List[Tuple[int, float]]
    averaged: bool = False
    caveat: str = ""


@dataclass
class LinearResult:
    """线性方程组求解结果。"""

    pi: Optional[List[float]]
    unique: bool
    rank: int
    residual: float
    class_distributions: List[Tuple[List[int], List[float]]] = field(default_factory=list)


@dataclass
class StationaryResult:
    """steady_state 高层接口的返回值。"""

    pi: List[float]
    method: str
    structure: Structure
    power: Optional[PowerResult] = None
    linear: Optional[LinearResult] = None


def validate_transition_matrix(
    P: Sequence[Sequence[float]], row_sum_tol: float = 1e-8
) -> List[List[float]]:
    """校验并返回浮点化的转移矩阵；行和不为 1 等非法情形抛出 InvalidChainError。"""

    if not isinstance(P, (list, tuple)) or len(P) == 0:
        raise InvalidChainError("转移矩阵必须是非空的二维列表/元组")
    n = len(P)
    rows: List[List[float]] = []
    for i, row in enumerate(P):
        if not isinstance(row, (list, tuple)) or len(row) != n:
            raise InvalidChainError(f"第 {i} 行长度为 {len(row) if hasattr(row, '__len__') else '?'}，矩阵必须为方阵（n={n}）")
        new_row: List[float] = []
        for j, value in enumerate(row):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidChainError(f"P[{i}][{j}]={value!r} 不是数值")
            fv = float(value)
            if not math.isfinite(fv):
                raise InvalidChainError(f"P[{i}][{j}]={fv} 不是有限数")
            if fv < 0.0:
                raise InvalidChainError(f"P[{i}][{j}]={fv} 为负，概率不允许为负")
            new_row.append(fv)
        row_sum = sum(new_row)
        if abs(row_sum - 1.0) > row_sum_tol:
            raise InvalidChainError(
                f"第 {i} 行元素之和为 {row_sum!r}，不等于 1（偏差 {abs(row_sum - 1.0):.3e} "
                f"超过容差 {row_sum_tol:.0e}）"
            )
        rows.append(new_row)
    return rows


def _mat_vec(P: Sequence[Sequence[float]], x: Sequence[float]) -> List[float]:
    n = len(P)
    out = [0.0] * n
    for i, pi in enumerate(x):
        if pi != 0.0:
            row = P[i]
            for j in range(n):
                out[j] += pi * row[j]
    return out


def stationary_residual(P: Sequence[Sequence[float]], pi: Sequence[float]) -> float:
    """平稳方程残差：||pi P - pi||_1；合法平稳分布应接近 0。"""

    rows = validate_transition_matrix(P)
    if len(pi) != len(rows):
        raise InvalidChainError(f"分布长度 {len(pi)} 与状态数 {len(rows)} 不一致")
    diff = _mat_vec(rows, pi)
    return sum(abs(diff[j] - pi[j]) for j in range(len(pi)))


def is_probability_distribution(
    pi: Sequence[float], tol: float = 1e-7, sum_tol: float = 1e-7
) -> bool:
    """判断向量是否非负且和为 1。"""

    return all(math.isfinite(v) and v >= -tol for v in pi) and abs(sum(pi) - 1.0) <= sum_tol


def _tarjan_scc(P: Sequence[Sequence[float]], tol: float) -> List[List[int]]:
    """Tarjan 强连通分量，按完成顺序返回（汇点 SCC 在前）。"""

    n = len(P)
    index = [-1] * n
    low = [0] * n
    on_stack = [False] * n
    stack: List[int] = []
    result: List[List[int]] = []
    counter = [0]

    def neighbors(u: int) -> List[int]:
        return [v for v in range(n) if P[u][v] > tol]

    def strong(v: int) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack[v] = True
        for w in neighbors(v):
            if index[w] == -1:
                strong(w)
                low[v] = min(low[v], low[w])
            elif on_stack[w]:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp: List[int] = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                comp.append(w)
                if w == v:
                    break
            result.append(comp)

    for s in range(n):
        if index[s] == -1:
            strong(s)
    return result


def _scc_period(P: Sequence[Sequence[float]], comp: Sequence[int], tol: float) -> int:
    """强连通类的周期：BFS 距离上所有边 |d[v]-d[u]-1| 的最大公约数。"""

    members = set(comp)
    start = comp[0]
    dist = {start: 0}
    queue = [start]
    head = 0
    period = 0
    while head < len(queue):
        u = queue[head]
        head += 1
        for v in range(len(P)):
            if v not in members or P[u][v] <= tol:
                continue
            if v not in dist:
                dist[v] = dist[u] + 1
                queue.append(v)
            period = math.gcd(period, abs(dist[v] - dist[u] - 1))
    return period if period > 0 else 1


def analyze_structure(P: Sequence[Sequence[float]], tol: float = 1e-12) -> Structure:
    """分析链的可约性、闭连通类数量与周期性（不依赖数值迭代）。"""

    rows = validate_transition_matrix(P)
    n = len(rows)
    sccs = _tarjan_scc(rows, tol)
    comp_of = {v: k for k, comp in enumerate(sccs) for v in comp}
    closed: List[List[int]] = []
    for comp in sccs:
        members = set(comp)
        is_closed = all(
            rows[u][v] <= tol for u in comp for v in range(n) if v not in members
        )
        if is_closed:
            closed.append(sorted(comp))

    periods = [_scc_period(rows, comp, tol) for comp in closed]
    irreducible = len(sccs) == 1
    unichain = len(closed) == 1
    aperiodic = all(d == 1 for d in periods)
    unique_stationary = unichain
    ergodic_for_power = unichain and aperiodic

    if unichain and aperiodic:
        reason = "单一闭类且非周期：幂迭代对任意初始分布收敛到唯一平稳分布"
    elif unichain and not aperiodic:
        reason = (
            f"唯一闭类但周期为 {periods[0]}：x_k P 周期振荡不收敛（线性解仍唯一，"
            "Cesaro 均值收敛）"
        )
    elif not unichain:
        reason = (
            f"存在 {len(closed)} 个闭连通类：平稳分布不唯一，幂迭代极限依赖初始分布"
            "（典型为多吸收态的吸收链 / 可约链）"
        )
    else:  # pragma: no cover - 逻辑兜底
        reason = "退化结构"

    return Structure(
        n=n,
        sccs=[sorted(c) for c in sccs],
        closed_classes=closed,
        irreducible=irreducible,
        unichain=unichain,
        periods=periods,
        aperiodic=aperiodic,
        ergodic_for_power=ergodic_for_power,
        unique_stationary=unique_stationary,
        reason=reason,
    )


def _solve_unit_stationary(Q: Sequence[Sequence[float]], pivot_tol: float) -> Tuple[Optional[List[float]], int]:
    """在单一闭类上解 (Q^T - I) pi = 0、sum(pi)=1；返回 (解或None, 秩)。

    系数矩阵取 Q^T-I 的第 1..n-1 行（去掉一行冗余齐次方程），
    最后一行放归一化方程 sum(pi)=1。
    """

    m = len(Q)
    A = [[Q[col][row] - (1.0 if col == row else 0.0) for col in range(m)]
         for row in range(1, m)]
    A.append([1.0] * m)
    b = [0.0] * (m - 1) + [1.0]
    return _gauss_jordan(A, b, pivot_tol)


def _gauss_jordan(A: List[List[float]], b: List[float], pivot_tol: float) -> Tuple[Optional[List[float]], int]:
    """部分选主元的高斯-若尔当消元；返回 (解或奇异时None, 有效秩)。"""

    n = len(b)
    M = [A[i][:] + [b[i]] for i in range(n)]
    rank = 0
    row = 0
    for col in range(n):
        pivot = max(range(row, n), key=lambda r: abs(M[r][col]))
        if abs(M[pivot][col]) <= pivot_tol:
            continue
        M[row], M[pivot] = M[pivot], M[row]
        pv = M[row][col]
        M[row] = [v / pv for v in M[row]]
        for r in range(n):
            if r != row:
                factor = M[r][col]
                if factor != 0.0:
                    M[r] = [M[r][j] - factor * M[row][j] for j in range(n + 1)]
        row += 1
        rank += 1
    if rank < n:
        return None, rank
    x = [0.0] * n
    for i in range(n):
        # 找主元列（简化后每行只有一个非零系数在对角附近）
        pivot_col = max(range(n), key=lambda c: abs(M[i][c]))
        x[pivot_col] = M[i][n]
    return x, rank


def stationary_linear(
    P: Sequence[Sequence[float]],
    require_unique: bool = False,
    pivot_tol: float = 1e-10,
) -> LinearResult:
    """线性方程组路径求解平稳分布。

    唯一平稳分布（单一闭类，含吸收链与周期链）时返回该分布；
    多个闭类时返回每个闭类各自的极值平稳分布，其任意凸组合都是平稳分布。
    """

    rows = validate_transition_matrix(P)
    n = len(rows)
    full_A = [[rows[col][row] - (1.0 if col == row else 0.0) for col in range(n)]
              for row in range(1, n)]
    full_A.append([1.0] * n)
    full_b = [0.0] * (n - 1) + [1.0]
    x, rank = _gauss_jordan([r[:] for r in full_A], full_b[:], pivot_tol)

    struct = analyze_structure(rows)
    if rank < n or not struct.unique_stationary:
        class_dist: List[Tuple[List[int], List[float]]] = []
        for comp in struct.closed_classes:
            m = len(comp)
            if m == 1:
                local = [1.0]
            else:
                Q = [[rows[u][v] for v in comp] for u in comp]
                local, local_rank = _solve_unit_stationary(Q, pivot_tol)
                if local is None:
                    raise RuntimeError(f"闭类 {comp} 上的平稳方程意外奇异（秩 {local_rank}/{m}）")
            full = [0.0] * n
            for idx, state in enumerate(comp):
                full[state] = local[idx]
            class_dist.append((comp, full))
        if require_unique:
            raise NonUniqueStationaryError(
                f"系数矩阵秩 {rank}/{n}，链有 {len(struct.closed_classes)} 个闭类，"
                "平稳分布不唯一（吸收/可约多闭类）；每个闭类的极值分布见 class_distributions"
            )
        return LinearResult(pi=None, unique=False, rank=rank, residual=float("nan"),
                            class_distributions=class_dist)

    assert x is not None
    x = [max(0.0, v) for v in x]
    total = sum(x)
    x = [v / total for v in x]
    residual = stationary_residual(rows, x)
    return LinearResult(pi=x, unique=True, rank=rank, residual=residual)


def power_iteration(
    P: Sequence[Sequence[float]],
    init: Optional[Sequence[float]] = None,
    *,
    max_iter: int = 100_000,
    tol: float = 1e-10,
    average: bool = False,
    stall_patience: int = 200,
    stall_eps: float = 1e-9,
    record_points: Optional[Sequence[int]] = None,
) -> PowerResult:
    """幂迭代路径：x_{k+1} = x_k P（average=True 时取 Cesaro 均值）。

    收敛 / 不收敛均返回，由调用方决定如何处理；结构原因可配合
    analyze_structure 得到确定性解释。stall 判定：连续 stall_patience 步
    残差改善小于 stall_eps，且残差仍高于 tol。
    """

    rows = validate_transition_matrix(P)
    n = len(rows)
    if init is None:
        x = [1.0 / n] * n
    else:
        if len(init) != n:
            raise InvalidChainError(f"初始分布长度 {len(init)} 与状态数 {n} 不一致")
        x = [float(v) for v in init]
        if any(v < 0.0 for v in x) or abs(sum(x) - 1.0) > 1e-8:
            raise InvalidChainError("初始分布必须非负且和为 1")

    record = sorted(set(record_points)) if record_points else []
    history: List[Tuple[int, float]] = [(0, stationary_residual(rows, x))]
    avg = x[:]
    best = history[0][1]
    stale = 0

    for k in range(1, max_iter + 1):
        x = _mat_vec(rows, x)
        if average:
            avg = [a + (v - a) / (k + 1) for a, v in zip(avg, x)]
            cur_vec = avg
        else:
            cur_vec = x
        residual = stationary_residual(rows, cur_vec)
        if record and k in record:
            history.append((k, residual))
        if not record and k in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000):
            history.append((k, residual))
        if residual < tol:
            history.append((k, residual))
            return PowerResult(pi=cur_vec[:], converged=True, iterations=k,
                               residual=residual, history=_dedup(history), averaged=average)
        if best - residual > stall_eps:
            best = min(best, residual)
            stale = 0
        else:
            stale += 1
        if stale >= stall_patience:
            history.append((k, residual))
            return PowerResult(pi=cur_vec[:], converged=False, iterations=k,
                               residual=residual, history=_dedup(history), averaged=average,
                               caveat=(f"残差在 {stall_patience} 步内改善小于 {stall_eps:.0e}，"
                                       "判定收敛停滞（周期链振荡，或多闭类依赖初值）"))

    history.append((max_iter, stationary_residual(rows, avg if average else x)))
    return PowerResult(pi=(avg if average else x)[:], converged=False, iterations=max_iter,
                       residual=history[-1][1], history=_dedup(history), averaged=average,
                       caveat=f"达到最大迭代次数 {max_iter} 仍未达到容差 {tol:.0e}")


def _dedup(history: List[Tuple[int, float]]) -> List[Tuple[int, float]]:
    seen = {}
    for k, r in history:
        seen[k] = r
    return [(k, seen[k]) for k in sorted(seen)]


def steady_state(
    P: Sequence[Sequence[float]],
    method: str = "auto",
    *,
    init: Optional[Sequence[float]] = None,
    max_iter: int = 100_000,
    tol: float = 1e-10,
) -> StationaryResult:
    """高层接口：先做结构判定，再按指定路径求解，异常情形给出明确说明。"""

    rows = validate_transition_matrix(P)
    struct = analyze_structure(rows)

    if method == "power":
        result = power_iteration(rows, init, max_iter=max_iter, tol=tol)
        if not result.converged:
            if not struct.unique_stationary:
                raise NonConvergenceError(
                    "幂迭代不收敛且极限依赖初始分布：" + struct.reason
                )
            raise NonConvergenceError(
                f"幂迭代不收敛：{struct.reason}；可改用 method='linear'（代数解唯一）"
                "或 average=True 的 Cesaro 均值"
            )
        return StationaryResult(pi=result.pi, method="power", structure=struct, power=result)

    if method == "linear":
        lin = stationary_linear(rows, require_unique=True)
        return StationaryResult(pi=lin.pi, method="linear", structure=struct, linear=lin)

    if method != "auto":
        raise ValueError("method 只能是 'auto'、'power' 或 'linear'")

    if not struct.unique_stationary:
        lin = stationary_linear(rows, require_unique=False)
        raise NonUniqueStationaryError(
            struct.reason + "；各闭类的极值平稳分布可由 stationary_linear(..., require_unique=False) 取得"
        )
    if struct.ergodic_for_power:
        result = power_iteration(rows, init, max_iter=max_iter, tol=tol)
        if not result.converged:
            raise NonConvergenceError(result.caveat or "幂迭代未收敛")
        return StationaryResult(pi=result.pi, method="power", structure=struct, power=result)

    lin = stationary_linear(rows, require_unique=True)
    return StationaryResult(pi=lin.pi, method="linear", structure=struct, linear=lin)
