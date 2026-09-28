"""连续分布函数：正态、Student-t、F。全部仅依赖 Python 标准库。

- 正态 CDF 用 math.erf；
- t / F 的 CDF 用正则化不完全 beta 函数 (连分式, Numerical Recipes)；
- 分位数 (ppf) 用区间倍增 + 二分求根，足够精确且无第三方依赖。
"""

import math

# ---------------------------------------------------------------------------
# 正态分布
# ---------------------------------------------------------------------------

def norm_cdf(z):
    """标准正态分布累积分布函数。"""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def norm_sf(z):
    """标准正态生存函数 P(Z > z)。"""
    return 1.0 - norm_cdf(z)


def norm_ppf(p):
    """标准正态分位数（反函数），精度约 1e-14。"""
    if not 0.0 < p < 1.0:
        raise ValueError("p 必须在 (0, 1) 内")
    lo, hi = -8.0, 8.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# 正则化不完全 beta 函数
# ---------------------------------------------------------------------------

def _betacf(a, b, x, max_iter=200, eps=3e-14):
    """连分式计算不完全 beta（Lentz 算法, Numerical Recipes betacf）。"""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-300:
        d = 1e-300
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a, b, x):
    """正则化不完全 beta 函数 I_x(a, b)，x 取 [0, 1]。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x)) / a
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x)
    return 1.0 - (math.exp(lbeta + b * math.log1p(-x) + a * math.log(x)) / b) \
        * _betacf(b, a, 1.0 - x)


# ---------------------------------------------------------------------------
# Student-t 分布
# ---------------------------------------------------------------------------

def t_cdf(t, df):
    """自由度为 df 的 Student-t 分布 CDF。"""
    if df <= 0:
        raise ValueError("自由度必须为正")
    x = df / (df + t * t)
    ib = 0.5 * betainc(df / 2.0, 0.5, x)
    return 1.0 - ib if t > 0 else ib


def t_sf(t, df):
    """t 分布生存函数 P(T > t)。"""
    return 1.0 - t_cdf(t, df)


def t_two_sided_p(t, df):
    """双侧 p 值 = P(|T| > |t|)。"""
    x = df / (df + t * t)
    return betainc(df / 2.0, 0.5, x)


def t_ppf(p, df):
    """Student-t 分位数：先倍增找包含区间，再二分。"""
    if not 0.0 < p < 1.0:
        raise ValueError("p 必须在 (0, 1) 内")
    if df <= 0:
        raise ValueError("自由度必须为正")
    if p < 0.5:
        return -t_ppf(1.0 - p, df)
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < p:
        lo, hi = hi, hi * 2.0
        if hi > 1e6:
            break
    for _ in range(90):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# F 分布
# ---------------------------------------------------------------------------

def f_cdf(x, df1, df2):
    """F 分布 CDF（方差比检验用）。"""
    if x <= 0.0:
        return 0.0
    y = (df1 * x) / (df1 * x + df2)
    return betainc(df1 / 2.0, df2 / 2.0, y)


def f_two_sided_p(f_stat, df1, df2):
    """F 双侧 p 值（用于双侧方差比检验）。"""
    if f_stat >= 1.0:
        tail = 1.0 - f_cdf(f_stat, df1, df2)
    else:
        tail = f_cdf(f_stat, df1, df2)
    return min(1.0, 2.0 * tail)
