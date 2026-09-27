"""一元非线性方程混合求根库（仅标准库）。

方法组合：
  1. 区间收缩法（二分法）：只要有变号包围区间 [lo, hi]（f(lo)*f(hi) < 0），
     根就被钉在区间内，每步区间长度减半，必然收敛。
  2. 切线法（牛顿法，未提供解析导数时退化为割线法）：在包围区间内部
     超线性收敛，作为加速手段。

切换判据（切线法 -> 区间法 的自动回退）：
  - 导数为零或绝对值小于 _DERIV_EPS（切线法失效）；
  - 牛顿步 x - f(x)/f'(x) 落在当前包围区间 (lo, hi) 之外（会发散）；
  - 牛顿步原地不动（步长为 0，停滞）。
  满足任一条件即改用二分步。无包围区间时（偶重根等不变号情形）使用
  阻尼牛顿法：若步长无法使 |f| 下降则逐步减半，20 次减半仍不能下降
  则判定失败，不会把残差很大的点当作根返回。

收敛状态（Status）：
  - CONVERGED:            收敛。判据为 |f(x)| <= ftol（残差判据），或
                          包围区间宽度 <= xtol（区间判据，根被变号区间钉死，
                          即使残差因函数病态而不小也是真根）。
  - NO_ROOT_IN_INTERVAL:  给定区间内 f 不变号，且网格扫描也找不到
                          残差足够小的点，判定区间内无根。
  - MAX_ITER_EXCEEDED:    达到迭代上限（或阻尼牛顿无法继续降低残差）
                          仍不收敛。此时 root=None，绝不返回伪根。

重根 / 导数接近零：
  - 收敛后估计根处导数 f'(root)，若 |f'(root)| < 1e-4（启发式阈值），
    置 multiple_root_suspected=True，提示可能是重根或平台。
  - 若残差无法压到 ftol 以下（如 (x-c)^2+eps 无实根），返回
    MAX_ITER_EXCEEDED 且 root=None。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

__all__ = ["Status", "RootResult", "solve"]

_DERIV_EPS = 1e-300  # 导数视为零的绝对阈值
_MULTI_DERIV_TOL = 1e-4  # 重根疑似判定的导数阈值（启发式）


class Status(Enum):
    CONVERGED = "converged"
    NO_ROOT_IN_INTERVAL = "no_root_in_interval"
    MAX_ITER_EXCEEDED = "max_iter_exceeded"


@dataclass
class RootResult:
    status: Status
    root: Optional[float] = None          # 根的估计；未收敛时为 None
    residual: Optional[float] = None      # |f(root)|
    iterations: int = 0                   # 主迭代次数
    f_evals: int = 0                      # f 的求值次数（不含 df）
    multiple_root_suspected: bool = False  # 根处导数近零，疑似重根/平台
    message: str = ""

    @property
    def converged(self) -> bool:
        return self.status is Status.CONVERGED


class _Counted:
    """包装目标函数，统计求值次数。"""

    def __init__(self, f: Callable[[float], float]):
        self._f = f
        self.n = 0

    def __call__(self, x: float) -> float:
        self.n += 1
        return self._f(x)


def solve(
    f: Callable[[float], float],
    a: Optional[float] = None,
    b: Optional[float] = None,
    x0: Optional[float] = None,
    df: Optional[Callable[[float], float]] = None,
    xtol: float = 1e-12,
    ftol: float = 1e-12,
    max_iter: int = 100,
    scan_points: int = 64,
    seed_tol: float = 1e-4,
) -> RootResult:
    """混合法求 f(x)=0 的根。

    参数：
      a, b: 求解区间。若 f(a)*f(b) < 0 直接形成包围区间；同号时先网格
            扫描寻找变号子区间或残差极小点（疑似偶重根，转阻尼牛顿）。
      x0:   仅给初值时，从 x0 出发倍增扩张搜索变号区间；找不到则直接
            阻尼牛顿。
      df:   解析导数（可选）。缺省时用割线/差商近似。
    """
    F = _Counted(f)

    def finish(status, root, it, msg, multi_check=False):
        res = None
        multi = False
        if root is not None:
            fr = F(root)
            res = abs(fr)
            if multi_check and status is Status.CONVERGED:
                h = 1e-7 * (1.0 + abs(root))
                fpx = (F(root + h) - fr) / h
                multi = abs(fpx) < _MULTI_DERIV_TOL
        return RootResult(status, root, res, it, F.n, multi, msg)

    bracket = None  # (lo, hi, flo, fhi)，保证 flo*fhi < 0
    seed = None     # 无包围区间时的牛顿初值

    if a is not None and b is not None:
        if a > b:
            a, b = b, a
        fa, fb = F(a), F(b)
        if fa == 0.0:
            return finish(Status.CONVERGED, a, 0, "区间端点 f(a)=0", True)
        if fb == 0.0:
            return finish(Status.CONVERGED, b, 0, "区间端点 f(b)=0", True)
        if fa * fb < 0.0:
            bracket = (a, b, fa, fb)
        else:
            # 同号：网格扫描找变号子区间或残差极小点
            best_x, best_f = (a, fa) if abs(fa) <= abs(fb) else (b, fb)
            prev_x, prev_f = a, fa
            for i in range(1, scan_points + 1):
                x = a + (b - a) * i / scan_points
                fx = F(x)
                if fx == 0.0:
                    return finish(Status.CONVERGED, x, 0, "扫描点处 f=0", True)
                if fx * prev_f < 0.0:
                    bracket = (prev_x, x, prev_f, fx)
                    break
                if abs(fx) < abs(best_f):
                    best_x, best_f = x, fx
                prev_x, prev_f = x, fx
            if bracket is None:
                if abs(best_f) <= seed_tol:
                    seed = best_x  # 疑似偶重根/平台，转阻尼牛顿
                else:
                    return finish(
                        Status.NO_ROOT_IN_INTERVAL, None, 0,
                        "区间内 f 不变号，扫描最小 |f|=%.3e" % abs(best_f))
    elif x0 is not None:
        f0 = F(x0)
        if f0 == 0.0:
            return finish(Status.CONVERGED, x0, 0, "初值处 f=0", True)
        # 倍增扩张搜索变号区间
        h = 1e-2 * (1.0 + abs(x0))
        left = right = x0
        fl = fr_ = f0
        for _ in range(60):
            h *= 2.0
            xl, xr = x0 - h, x0 + h
            fxl, fxr = F(xl), F(xr)
            if fxl * fl < 0.0:
                bracket = (xl, left, fxl, fl)
                break
            if fxr * fr_ < 0.0:
                bracket = (right, xr, fr_, fxr)
                break
            left, fl = xl, fxl
            right, fr_ = xr, fxr
        if bracket is None:
            seed = x0
    else:
        raise ValueError("必须提供区间 [a, b] 或初值 x0")

    if bracket is not None:
        how, x, it = _hybrid_bracket(F, df, bracket, x0, xtol, ftol, max_iter)
        if how == "residual":
            return finish(Status.CONVERGED, x, it,
                          "收敛：|f(x)| <= ftol（残差判据）", True)
        if how == "bracket":
            return finish(Status.CONVERGED, x, it,
                          "收敛：包围区间宽度 <= xtol（区间判据，根被变号区间钉死）",
                          True)
        return finish(Status.MAX_ITER_EXCEEDED, None, it,
                      "达到迭代上限 %d 仍不收敛；最优近似 x=%.17g, |f|=%.3e"
                      % (max_iter, x, abs(F(x))))

    # 无包围区间：阻尼牛顿（用于偶重根等不变号情形）
    how, x, fx, it = _damped_newton(F, df, seed, xtol, ftol, max_iter)
    if how == "residual":
        return finish(Status.CONVERGED, x, it,
                      "收敛（阻尼牛顿，无变号区间）：|f(x)| <= ftol", True)
    if how == "deriv_zero":
        return finish(Status.MAX_ITER_EXCEEDED, None, it,
                      "导数接近零且残差不满足容差（|f|=%.3e），疑似无实根的平台"
                      % abs(fx))
    if how == "stagnate":
        return finish(Status.MAX_ITER_EXCEEDED, None, it,
                      "阻尼牛顿无法继续降低残差（|f|=%.3e），不把该点当作根"
                      % abs(fx))
    return finish(Status.MAX_ITER_EXCEEDED, None, it,
                  "达到迭代上限 %d 仍不收敛（|f|=%.3e）" % (max_iter, abs(fx)))


def _derivative(F, df, x, fx, x_prev, fx_prev):
    """解析导数优先，否则割线，首次用前向差商。"""
    if df is not None:
        return df(x)
    if x_prev is not None and x != x_prev:
        return (fx - fx_prev) / (x - x_prev)
    h = 1e-8 * (1.0 + abs(x))
    return (F(x + h) - fx) / h


def _hybrid_bracket(F, df, bracket, x0, xtol, ftol, max_iter):
    """Safeguarded Newton–bisection。返回 (收敛方式, x, 迭代数)。"""
    lo, hi, flo, fhi = bracket
    if x0 is not None and lo < x0 < hi:
        x = x0
    else:
        x = 0.5 * (lo + hi)
    fx = F(x)
    x_prev = fx_prev = None

    for it in range(1, max_iter + 1):
        if abs(fx) <= ftol:
            return "residual", x, it
        if hi - lo <= xtol:
            return "bracket", 0.5 * (lo + hi), it

        fpx = _derivative(F, df, x, fx, x_prev, fx_prev)

        # 切换判据：导数近零 / 牛顿步出界 / 牛顿步停滞 -> 二分
        xn = None
        if abs(fpx) > _DERIV_EPS:
            cand = x - fx / fpx
            if lo < cand < hi and cand != x:
                xn = cand
        if xn is None:
            xn = 0.5 * (lo + hi)

        fn = F(xn)
        if flo * fn < 0.0:
            hi, fhi = xn, fn
        else:
            lo, flo = xn, fn
        x_prev, fx_prev = x, fx
        x, fx = xn, fn

    return "max_iter", x, max_iter


def _damped_newton(F, df, x, xtol, ftol, max_iter):
    """阻尼牛顿（无包围区间）。返回 (收敛方式, x, f(x), 迭代数)。"""
    fx = F(x)
    x_prev = fx_prev = None
    for it in range(1, max_iter + 1):
        if abs(fx) <= ftol:
            return "residual", x, fx, it
        fpx = _derivative(F, df, x, fx, x_prev, fx_prev)
        if abs(fpx) <= _DERIV_EPS:
            return "deriv_zero", x, fx, it
        step = fx / fpx
        t = 1.0
        accepted = False
        for _ in range(20):  # 步长减半回溯，必须使 |f| 严格下降
            xn = x - t * step
            fn = F(xn)
            if abs(fn) < abs(fx):
                accepted = True
                break
            t *= 0.5
        if not accepted:
            return "stagnate", x, fx, it
        if abs(xn - x) <= xtol * (1.0 + abs(xn)) and abs(fn) > ftol:
            return "stagnate", xn, fn, it
        x_prev, fx_prev = x, fx
        x, fx = xn, fn
    return "max_iter", x, fx, max_iter
