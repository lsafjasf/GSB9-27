"""rootfind — 一元非线性方程求根库（仅使用 Python 标准库）。

方法组合
--------
1. 区间收缩（二分法）：只要包围区间两端函数值异号，二分永远收敛，作为兜底。
2. 切线法（Newton 迭代）：靠近单根时二阶收敛，作为主加速手段；
   检测到重根时自动估计重数 m 并改用修正切线法 x <- x - m*f/f'。

切换判据（切线法 -> 区间法自动退回）
------------------------------------
出现以下任一情况时，本步放弃切线步、改用二分步：
  a) 导数为零或接近零：|f'(x)| <= 1e-14 * (1 + |f(x)|)；
  b) 切线步落点跑到当前包围区间 (lo, hi) 之外或为非有限值；
  c) 切线步落点处函数无定义（如 1/x 的极点）；
  d) 步长已停滞（<= xtol）但残差仍大，强制连续二分若干步。

重数估计：相邻两次切线步的步长比 q 趋于常数且落在 (0.3, 0.95) 时，
说明是线性收敛（重根特征），由 q = 1 - 1/m 解出重数 m 并取整；
若加速步反而使残差显著增大，则放弃加速、退回 m = 1。

收敛状态（Status）
-----------------
  CONVERGED                收敛，root/residual 有效
  NO_ROOT_IN_INTERVAL      区间端点同号且未探测到切触根（偶重根）
  MAX_ITER_EXCEEDED        达到迭代上限仍不收敛
  STALLED_ZERO_DERIVATIVE  无区间模式下导数接近零且残差不小，无法继续
  POLE_SUSPECTED           区间已收缩到容差但残差仍很大，疑似极点/间断
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Tuple

__all__ = ["Status", "RootResult", "find_root"]

_DF_TINY = 1e-14                       # 导数“接近零”的相对阈值
_SQRT_EPS = 1.4901161193847656e-08     # sqrt(双精度机器 epsilon)，数值微分步长基数


class Status(Enum):
    CONVERGED = "converged"
    NO_ROOT_IN_INTERVAL = "no_root_in_interval"
    MAX_ITER_EXCEEDED = "max_iter_exceeded"
    STALLED_ZERO_DERIVATIVE = "stalled_zero_derivative"
    POLE_SUSPECTED = "pole_suspected"


@dataclass
class RootResult:
    status: Status
    root: Optional[float] = None       # 根的估计；仅 CONVERGED 时保证有效
    residual: Optional[float] = None   # |f(root)|
    iterations: int = 0                # 迭代步数
    f_evals: int = 0                   # f 的求值次数（含数值微分消耗）
    df_evals: int = 0                  # 解析导数 df 的求值次数
    multiplicity: int = 1              # 估计出的重根重数（1 表示单根）
    message: str = ""

    @property
    def converged(self) -> bool:
        return self.status is Status.CONVERGED


class _Counter:
    """包装可调用对象并统计调用次数；把求值异常统一映射为 nan。"""

    def __init__(self, fn: Callable[[float], float]):
        self._fn = fn
        self.n = 0

    def __call__(self, x: float) -> float:
        self.n += 1
        try:
            return self._fn(x)
        except (ArithmeticError, ValueError):
            return math.nan


def find_root(
    f: Callable[[float], float],
    *,
    bracket: Optional[Tuple[float, float]] = None,
    x0: Optional[float] = None,
    df: Optional[Callable[[float], float]] = None,
    xtol: float = 1e-12,
    ftol: float = 1e-12,
    max_iter: int = 100,
) -> RootResult:
    """求 f(x) = 0 的一个根。

    参数：
        f        目标函数
        bracket  (a, b) 包围区间；若 f(a)、f(b) 异号则启用“切线+二分”混合法
        x0       初值；无区间时单独使用（纯切线法），有区间时作为起始点
        df       解析导数；缺省时用中心差分（每次耗 2 次 f 求值）
        xtol     自变量容差
        ftol     残差容差 |f(x)|
        max_iter 迭代上限
    """
    if xtol <= 0 or ftol <= 0:
        raise ValueError("xtol 与 ftol 必须为正数")
    if max_iter < 1:
        raise ValueError("max_iter 必须 >= 1")
    if bracket is None and x0 is None:
        raise ValueError("必须提供 bracket=(a, b) 或初值 x0")

    fc = _Counter(f)
    dfc = _Counter(df) if df is not None else None

    def deriv(x: float) -> float:
        if dfc is not None:
            return dfc(x)
        h = _SQRT_EPS * (1.0 + abs(x))
        return (fc(x + h) - fc(x - h)) / (2.0 * h)

    def finish(status, root, residual, it, m, msg) -> RootResult:
        return RootResult(status, root, residual, it, fc.n,
                          dfc.n if dfc is not None else 0, m, msg)

    if bracket is not None:
        a, b = float(bracket[0]), float(bracket[1])
        if not a < b:
            raise ValueError("bracket 必须满足 a < b")
        fa, fb = fc(a), fc(b)
        if not (math.isfinite(fa) and math.isfinite(fb)):
            raise ValueError("区间端点处函数无定义")
        if abs(fa) <= ftol:
            return finish(Status.CONVERGED, a, abs(fa), 0, 1, "左端点处 |f| 已达容差")
        if abs(fb) <= ftol:
            return finish(Status.CONVERGED, b, abs(fb), 0, 1, "右端点处 |f| 已达容差")
        if (fa < 0.0) != (fb < 0.0):
            # 端点异号：切线 + 二分混合
            x = float(x0) if (x0 is not None and a < x0 < b) else 0.5 * (a + b)
            fx = fc(x)
            return _hybrid(fc, deriv, a, b, fa, fb, x, fx,
                           xtol, ftol, max_iter, finish)
        # 端点同号：用阻尼切线法从中点探测偶重根（切触根）
        probe = _newton(fc, deriv, 0.5 * (a + b), xtol, ftol,
                        min(max_iter, 60), finish)
        if probe.converged:
            probe.message = "区间端点同号，但内部探测到偶重根（切触根）"
            return probe
        return finish(Status.NO_ROOT_IN_INTERVAL, None, None,
                      probe.iterations, 1,
                      "f(a) 与 f(b) 同号，且未探测到切触根，判定区间内无根")

    # 无区间：纯阻尼切线法
    return _newton(fc, deriv, float(x0), xtol, ftol, max_iter, finish)


def _hybrid(fc, deriv, lo, hi, flo, fhi, x, fx, xtol, ftol, max_iter, finish):
    """切线法 + 二分混合（区间始终保有根），含重根重数估计与加速。"""
    m = 1                     # 重数估计
    q_hist = []               # 相邻切线步步长比
    s_prev = None             # 上一步切线步长 |f/f'|
    prev_was_newton = False
    scale_f = max(1.0, abs(flo), abs(fhi))
    force_bisect = 0          # 步长停滞时强制二分的剩余步数

    for it in range(1, max_iter + 1):
        if abs(fx) <= ftol:
            return finish(Status.CONVERGED, x, abs(fx), it - 1, m, "残差达到容差")

        dfx = deriv(x)
        s = None
        if math.isfinite(dfx) and abs(dfx) > _DF_TINY * (1.0 + abs(fx)):
            s = m * fx / dfx  # 判据 a)：导数接近零则 s 保持 None

        cand = None
        if s is not None and force_bisect == 0:
            c = x - s
            if math.isfinite(c) and lo < c < hi:
                cand = c      # 判据 b)：落点必须在包围区间内
        used_newton = cand is not None
        if cand is None:
            cand = 0.5 * (lo + hi)   # 退回区间收缩

        fxn = fc(cand)
        if not math.isfinite(fxn):
            if used_newton:  # 判据 c)：切线步落到奇点，退回二分
                used_newton = False
                cand = 0.5 * (lo + hi)
                fxn = fc(cand)
            if not math.isfinite(fxn):
                return finish(Status.POLE_SUSPECTED, None, None, it, m,
                              "区间中点处函数无定义，疑似极点/间断")

        # ---- 重数估计：q = |s_n / s_{n-1}| -> 1 - 1/m ----
        if used_newton and prev_was_newton and m == 1 and s_prev:
            q = abs(s) / s_prev
            if 0.3 < q < 0.95:
                q_hist.append(q)
                if len(q_hist) >= 2 and abs(q_hist[-1] - q_hist[-2]) <= 0.1 * q_hist[-1]:
                    est = 1.0 / (1.0 - 0.5 * (q_hist[-1] + q_hist[-2]))
                    mi = int(round(est))
                    if 2 <= mi <= 16 and abs(est - mi) < 0.3:
                        m = mi
                        q_hist.clear()
        # 加速失败检测：修正切线步使残差显著增大 -> 退回 m = 1
        if used_newton and m > 1 and abs(fxn) > 2.0 * abs(fx):
            m = 1
            q_hist.clear()

        s_prev = abs(s) if (used_newton and s is not None) else None
        prev_was_newton = used_newton

        if fxn == 0.0:
            return finish(Status.CONVERGED, cand, 0.0, it, m, "命中精确根")
        # 更新包围区间（保持两端异号）
        if (flo < 0.0) != (fxn < 0.0):
            hi, fhi = cand, fxn
        else:
            lo, flo = cand, fxn

        step = abs(cand - x)
        x, fx = cand, fxn
        if force_bisect > 0:
            force_bisect -= 1

        # 重根残差放宽：|x - 根| ~ xtol 时 |f| 只能保证 ~ xtol^m
        relaxed = max(ftol, xtol ** m) * scale_f

        if step <= xtol * (1.0 + abs(x)):
            if abs(fx) <= relaxed:
                return finish(Status.CONVERGED, x, abs(fx), it, m,
                              "步长收敛（重根残差容差已按重数放宽）")
            force_bisect = 3  # 判据 d)：步长停滞但残差大，强制二分

        if (hi - lo) <= xtol * (1.0 + abs(x)):
            if abs(fx) <= relaxed:
                return finish(Status.CONVERGED, x, abs(fx), it, m, "区间收敛")
            return finish(Status.POLE_SUSPECTED, x, abs(fx), it, m,
                          "区间已收缩至容差但残差仍大，疑似极点/间断或函数过陡")

    if abs(fx) <= ftol:
        return finish(Status.CONVERGED, x, abs(fx), max_iter, m, "残差达到容差")
    return finish(Status.MAX_ITER_EXCEEDED, None, abs(fx), max_iter, m,
                  "达到迭代上限，残差仍不满足容差")


def _newton(fc, deriv, x0, xtol, ftol, max_iter, finish):
    """无区间保护的阻尼切线法（用于仅给初值、或同号区间探测切触根）。"""
    x = float(x0)
    fx = fc(x)
    for it in range(1, max_iter + 1):
        if abs(fx) <= ftol:
            return finish(Status.CONVERGED, x, abs(fx), it - 1, 1, "残差达到容差")
        dfx = deriv(x)
        if not math.isfinite(dfx) or abs(dfx) <= _DF_TINY * (1.0 + abs(fx)):
            return finish(Status.STALLED_ZERO_DERIVATIVE, None, abs(fx), it - 1, 1,
                          "导数接近零且残差不小，切线法无法继续")
        step = fx / dfx
        # 有限阻尼：最多减半 6 次；仍不下降则接受该步，靠迭代上限兜底
        lam = 1.0
        xn = x - step
        fxn = fc(xn) if math.isfinite(xn) else math.nan
        while (not math.isfinite(fxn) or abs(fxn) >= abs(fx)) and lam > 1.0 / 64.0:
            lam *= 0.5
            xn = x - lam * step
            fxn = fc(xn) if math.isfinite(xn) else math.nan
        if not math.isfinite(fxn):
            return finish(Status.MAX_ITER_EXCEEDED, None, abs(fx), it, 1,
                          "迭代发散（函数值非有限）")
        if abs(xn - x) <= xtol * (1.0 + abs(xn)) and abs(fxn) > ftol:
            return finish(Status.STALLED_ZERO_DERIVATIVE, None, abs(fxn), it, 1,
                          "步长停滞且残差不小")
        x, fx = xn, fxn
    if abs(fx) <= ftol:
        return finish(Status.CONVERGED, x, abs(fx), max_iter, 1, "残差达到容差")
    return finish(Status.MAX_ITER_EXCEEDED, None, abs(fx), max_iter, 1,
                  "达到迭代上限，残差仍不满足容差")
