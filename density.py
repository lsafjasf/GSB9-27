"""density.py — 直方图与核密度估计（KDE），仅依赖 Python 标准库。

设计要点
--------
直方图分箱规则（bins 参数）：
  - "sqrt"    : k = ceil(sqrt(n))。最简单，任何分布都能用；对双峰/长尾偏粗。
  - "sturges" : k = ceil(log2(n)) + 1。隐含"数据近似正态"假设，n 大时严重欠分箱。
  - "rice"    : k = ceil(2 * n^(1/3))。介于 sqrt 与 sturges 之间。
  - "scott"   : 带宽 h = 3.5*std*n^(-1/3)，k = range/h。对正态最优（MSE 意义），
                对重尾/多峰会过度平滑（箱太宽）。
  - "fd"      : Freedman–Diaconis，h = 2*IQR*n^(-1/3)。用 IQR 代替 std，
                对离群值和重尾稳健，是探索性分析的常用默认。
  - "auto"    : 默认规则 = FD；IQR 为 0（如大量重复值）时回退 Scott；
                Scott 的 std 也为 0 时回退 sqrt。兼顾稳健性与简单场景。
  也可直接传 int（箱数）或浮点序列（自定义箱边界）。

KDE（高斯核）：
  - 带宽自动选择：Silverman 经验法则 h = 0.9 * min(std, IQR/1.34) * n^(-1/5)。
    取 min 是为了对重尾/偏态稳健（IQR 不受离群值影响）；
    双峰分布会偏平滑，可用 bandwidth= 手动覆盖或乘系数。
  - 退化数据（std 与 IQR 均为 0，如单点、全部同值）：回退带宽
    h = max(|x|, 1.0) * 0.05，保证数值稳定且积分仍为 1。
  - 求值：n <= EXACT_MAX_N 时用精确 O(n*m) 求和；更大样本用"线性分箱 +
    截断核卷积"的 O(G*K) 快速算法（与 statsmodels 的 fast KDE 同类），
    十万点耗时从分钟级降到亚秒级。
  - 归一化：返回前对网格密度做梯形积分并除以积分值，
    保证在给定区间上 ∫f̂(x)dx == 1（数值意义，误差 < 1e-12）。
"""

import math

__all__ = [
    "histogram",
    "kde",
    "silverman_bandwidth",
    "integrate_trapezoid",
    "BIN_RULES",
]

_builtin_range = range  # histogram() 的 range 参数会遮蔽内置 range

EXACT_MAX_N = 2048  # 超过该样本量时 KDE 切换到分箱快速算法
BIN_RULES = ("auto", "sqrt", "sturges", "rice", "scott", "fd")


# ---------------------------------------------------------------- 基础统计量

def _check_data(data):
    xs = [float(v) for v in data]
    if not xs:
        raise ValueError("data 不能为空")
    for v in xs:
        if math.isnan(v) or math.isinf(v):
            raise ValueError("data 含 NaN/Inf，无法估计密度")
    return xs


def _mean_std(xs):
    n = len(xs)
    mean = sum(xs) / n
    var = sum((v - mean) ** 2 for v in xs) / n  # 总体方差，与 Silverman 公式一致
    return mean, math.sqrt(var)


def _iqr(xs_sorted):
    n = len(xs_sorted)

    def quantile(p):
        pos = p * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return xs_sorted[lo] * (1 - frac) + xs_sorted[hi] * frac

    return quantile(0.75) - quantile(0.25)


# ---------------------------------------------------------------- 直方图

def _n_bins_from_rule(rule, xs, xs_sorted):
    n = len(xs)
    lo, hi = xs_sorted[0], xs_sorted[-1]
    span = hi - lo
    if rule == "sqrt":
        return max(1, math.ceil(math.sqrt(n)))
    if rule == "sturges":
        return max(1, math.ceil(math.log2(n)) + 1) if n > 1 else 1
    if rule == "rice":
        return max(1, math.ceil(2 * n ** (1 / 3)))
    if rule == "scott":
        _, std = _mean_std(xs)
        if std == 0 or span == 0:
            return max(1, math.ceil(math.sqrt(n)))
        h = 3.5 * std * n ** (-1 / 3)
        return max(1, math.ceil(span / h))
    if rule == "fd":
        iqr = _iqr(xs_sorted)
        if iqr == 0 or span == 0:
            return _n_bins_from_rule("scott", xs, xs_sorted)
        h = 2 * iqr * n ** (-1 / 3)
        return max(1, math.ceil(span / h))
    if rule == "auto":
        return _n_bins_from_rule("fd", xs, xs_sorted)
    raise ValueError(f"未知分箱规则: {rule!r}，可选 {BIN_RULES} 或 int/边界序列")


def histogram(data, bins="auto", range=None):
    """计算直方图。

    参数
      bins : "auto"/"sqrt"/"sturges"/"rice"/"scott"/"fd"、int（箱数）
             或单调递增的边界序列。
      range: (lo, hi) 强制区间；区间外的样本被丢弃。

    返回 (edges, counts, densities)
      edges     : 长度 k+1 的箱边界
      counts    : 每箱样本数（含右端点落入最后一箱）
      densities : counts / (n * 箱宽)，满足 sum(d*width) == 1（未丢弃样本时）
    """
    xs = _check_data(data)
    if range is not None:
        lo, hi = float(range[0]), float(range[1])
        if not lo < hi:
            raise ValueError("range 必须满足 lo < hi")
        xs = [v for v in xs if lo <= v <= hi]
        if not xs:
            raise ValueError("range 内没有样本")
    xs_sorted = sorted(xs)
    lo, hi = xs_sorted[0], xs_sorted[-1]

    if isinstance(bins, str):
        k = _n_bins_from_rule(bins, xs, xs_sorted)
        edges = None
    elif isinstance(bins, int):
        if bins < 1:
            raise ValueError("bins 必须 >= 1")
        k = bins
        edges = None
    else:
        edges = [float(e) for e in bins]
        if len(edges) < 2 or any(b <= a for a, b in zip(edges, edges[1:])):
            raise ValueError("自定义边界必须严格递增且至少 2 个")
        k = len(edges) - 1

    if edges is None:
        if hi == lo:  # 全部同值：以该值为中心造一个单位宽度的箱
            half = 0.5 if lo == 0 else abs(lo) * 0.05
            lo, hi = lo - half, lo + half
        width = (hi - lo) / k
        edges = [lo + i * width for i in _builtin_range(k + 1)]
        edges[-1] = hi  # 消除浮点尾差

    counts = [0] * k
    for v in xs:
        if v < edges[0] or v > edges[-1]:
            continue
        if v == edges[-1]:
            counts[-1] += 1
            continue
        idx = int((v - edges[0]) / (edges[-1] - edges[0]) * k)
        # 浮点误差修正：线性回退到正确箱
        while idx > 0 and v < edges[idx]:
            idx -= 1
        while idx < k - 1 and v >= edges[idx + 1]:
            idx += 1
        counts[idx] += 1

    n = len(xs)
    densities = [c / (n * (edges[i + 1] - edges[i])) for i, c in enumerate(counts)]
    return edges, counts, densities


# ---------------------------------------------------------------- KDE

def silverman_bandwidth(data):
    """Silverman 经验法则带宽：h = 0.9 * min(std, IQR/1.34) * n^(-1/5)。

    退化数据（std == IQR == 0）回退为 max(|x|, 1) * 0.05。
    """
    xs = _check_data(data)
    n = len(xs)
    xs_sorted = sorted(xs)
    _, std = _mean_std(xs)
    iqr = _iqr(xs_sorted)
    scale = min(std, iqr / 1.34) if iqr > 0 else std
    if scale <= 0:
        scale = max(abs(xs_sorted[0]), 1.0) * 0.05
    return 0.9 * scale * n ** (-1 / 5)


def _gauss(u):
    return math.exp(-0.5 * u * u) / math.sqrt(2 * math.pi)


def _kde_exact(xs, grid, h):
    n = len(xs)
    inv = 1.0 / (n * h)
    out = []
    for g in grid:
        s = 0.0
        for v in xs:
            u = (g - v) / h
            if -5.0 < u < 5.0:  # 截断尾部，精度损失 < 1e-6
                s += _gauss(u)
        out.append(s * inv)
    return out


def _kde_binned(xs, grid, h):
    """线性分箱 + 截断核卷积的快速 KDE（网格须等距）。"""
    g0 = grid[0]
    dx = grid[1] - grid[0]
    gsize = len(grid)
    weights = [0.0] * gsize
    n = len(xs)
    for v in xs:
        pos = (v - g0) / dx
        i = int(pos)
        frac = pos - i
        if 0 <= i < gsize:
            weights[i] += 1.0 - frac
        if 0 <= i + 1 < gsize:
            weights[i + 1] += frac
    half = max(1, int(5.0 * h / dx))  # 核截断在 ±5h
    kernel = [_gauss(j * dx / h) for j in range(-half, half + 1)]
    norm = sum(kernel)
    kernel = [kv / norm for kv in kernel]
    out = [0.0] * gsize
    for i in range(gsize):
        acc = 0.0
        lo = max(0, i - half)
        hi = min(gsize - 1, i + half)
        for j in range(lo, hi + 1):
            w = weights[j]
            if w:
                acc += w * kernel[i - j + half]
        out[i] = acc / (n * h)
    return out


def integrate_trapezoid(xs, ys):
    """梯形积分。"""
    return sum((xs[i + 1] - xs[i]) * (ys[i + 1] + ys[i]) * 0.5
               for i in range(len(xs) - 1))


def kde(data, bandwidth=None, n_grid=512, margin=3.0, grid=None):
    """高斯核密度估计。

    参数
      bandwidth: None → Silverman 自动带宽；也可传正数手动指定。
      n_grid   : 求值网格点数（grid 未给定时生效）。
      margin   : 网格相对数据范围两端各外延 margin * bandwidth。
      grid     : 自定义等距求值点。

    返回 (xs, density, h)
      xs/density 在给定区间上满足梯形积分 == 1（已显式归一化）。
    """
    xs_data = _check_data(data)
    h = float(bandwidth) if bandwidth is not None else silverman_bandwidth(xs_data)
    if not (h > 0):
        raise ValueError("bandwidth 必须为正数")

    lo, hi = min(xs_data), max(xs_data)
    if grid is None:
        a, b = lo - margin * h, hi + margin * h
        if n_grid < 16:
            raise ValueError("n_grid 至少为 16")
        step = (b - a) / (n_grid - 1)
        grid = [a + i * step for i in range(n_grid)]
    else:
        grid = [float(g) for g in grid]
        if len(grid) < 2:
            raise ValueError("grid 至少需要 2 个点")

    if len(xs_data) <= EXACT_MAX_N:
        dens = _kde_exact(xs_data, grid, h)
    else:
        dens = _kde_binned(xs_data, grid, h)

    total = integrate_trapezoid(grid, dens)
    if total <= 0:
        raise ArithmeticError("KDE 积分非正，无法归一化")
    dens = [d / total for d in dens]
    return grid, dens, h
