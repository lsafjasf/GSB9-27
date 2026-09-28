"""参照实现：离线批量最小二乘（带先验的随机游走 MAP 估计）与常数模型 WLS。

这些是“用全部数据一次性求解”的离线解，用于和在线卡尔曼滤波对拍。

批量估计最小化
    (x0-x_init)^2/p_init
      + sum_k (x[k]-x[k-1])^2/q          （过程项，q=0 时令 x 恒定）
      + sum_{观测到齐 k} (z[k]-x[k])^2/r[k]

对所有 x[k] 这是线性方程组（三对角），用 Thomas 算法（标准库实现）求解。
关键对拍性质（见 selftest）：
  1. q=0（常数模型）时，带先验递推 WLS 的每一步都与在线 KF 完全一致；
  2. 一般 q>=0 时，批量解的“末点”等于在线 KF 的末点估计
     （前向滤波就是末点的递归最小二乘解），后验方差也一致。
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple


def _thomas(lower: List[float], diag: List[float], upper: List[float],
            rhs: List[float]) -> List[float]:
    """解三对角线性方程组；会就地修改传入数组。"""
    n = len(diag)
    if n == 1:
        return [rhs[0] / diag[0]]
    for i in range(1, n):
        m = lower[i] / diag[i - 1]
        diag[i] -= m * upper[i - 1]
        rhs[i] -= m * rhs[i - 1]
    sol = [0.0] * n
    sol[-1] = rhs[-1] / diag[-1]
    for i in range(n - 2, -1, -1):
        sol[i] = (rhs[i] - upper[i] * sol[i + 1]) / diag[i]
    return sol


def batch_smooth(
    zs: Sequence[Optional[float]],
    q: float,
    rs: Sequence[float],
    x_init: float,
    p_init: float,
) -> Tuple[List[float], list]:
    """带高斯先验的批量 MAP 估计（平滑解）。

    返回 (xs, information_matrix)；rs 为每个时刻的观测方差（缺失时刻忽略）。
    """
    n = len(zs)
    if n == 0:
        return [], []
    if q == 0.0:
        # 常数模型：单变量加权最小二乘 + 先验
        prec = 1.0 / p_init
        mean_acc = x_init / p_init
        for z, r in zip(zs, rs):
            if z is not None and not (isinstance(z, float) and math.isnan(z)):
                prec += 1.0 / r
                mean_acc += z / r
        x = mean_acc / prec
        H = [[prec]]
        return [x] * n, H

    inv_q = 1.0 / q
    lower = [0.0] * n
    diag = [0.0] * n
    upper = [0.0] * n
    rhs = [0.0] * n

    diag[0] = 1.0 / p_init + inv_q
    rhs[0] = x_init / p_init
    for k in range(1, n):
        # 内部点与前后两个邻居相连（2/q）；末点只与前一个邻居相连（1/q）
        diag[k] = (inv_q + inv_q) if k < n - 1 else inv_q
        lower[k] = -inv_q
        upper[k - 1] = -inv_q
    z0 = zs[0]
    if z0 is not None and not (isinstance(z0, float) and math.isnan(z0)):
        diag[0] += 1.0 / rs[0]
        rhs[0] += z0 / rs[0]
    for k in range(1, n):
        z = zs[k]
        if z is not None and not (isinstance(z, float) and math.isnan(z)):
            diag[k] += 1.0 / rs[k]
            rhs[k] += z / rs[k]

    xs = _thomas(lower, diag, upper, rhs)
    return xs, []


def wls_constant_online(
    zs: Sequence[Optional[float]],
    rs: Sequence[float],
    x_init: float,
    p_init: float,
) -> List[Tuple[float, float, Optional[float]]]:
    """常数模型（q=0）递推加权最小二乘：信息形式逐点累积。

    每步返回 (估计, 方差, 残差 z - 估计_pred)。这是手工可推导的参照序列，
    且与 KF 在 q=0 时的递推数学上完全相同。
    """
    precision = 1.0 / p_init
    info_mean = x_init / p_init
    x = x_init
    out = []
    for z, r in zip(zs, rs):
        if z is None or (isinstance(z, float) and math.isnan(z)):
            out.append((x, 1.0 / precision, None))
            continue
        x_pred = x
        p_pred = 1.0 / precision
        precision += 1.0 / r
        x = (info_mean + z / r) / precision
        info_mean = precision * x
        out.append((x, 1.0 / precision, z - x_pred))
    return out
