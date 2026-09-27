"""density.py — 直方图与核密度估计（仅标准库）。

直方图：可配置分箱规则（sqrt / sturges / rice / scott / fd / auto / 固定 k）。
KDE：高斯核，Silverman 经验法则自动带宽，线性分箱加速（十万点亚秒级）。
"""

import math

__all__ = [
    "histogram", "bin_edges", "auto_bin_count",
    "silverman_bandwidth", "kde", "kde_grid", "integrate",
]


# ---------------------------------------------------------------- 基础统计量

def _mean(xs):
    return sum(xs) / len(xs)


def _std(xs, mean=None):
    if mean is None:
        mean = _mean(xs)
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / len(xs))


def _quantile(sorted_xs, q):
    """线性插值分位数，sorted_xs 必须已排序。"""
    n = len(sorted_xs)
    if n == 1:
        return sorted_xs[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac


def _iqr(sorted_xs):
    return _quantile(sorted_xs, 0.75) - _quantile(sorted_xs, 0.25)


# ---------------------------------------------------------------- 直方图

def auto_bin_count(data, rule="auto"):
    """按规则返回分箱数 k（>=1）。

    规则取舍：
      sqrt    —— 最简单，k=ceil(sqrt(n))；小样本偏粗，大样本偏细。
      sturges —— k=ceil(log2 n)+1；只适合近似对称、中小样本，大样本严重欠拟合。
      rice    —— k=ceil(2 n^(1/3))；介于 sqrt 与 sturges 之间。
      scott   —— 基于标准差的“最优”等宽 bin（正态假设下渐近最优）；
                 对重尾/离群值敏感，容易把 bin 拉得过宽。
      fd      —— Freedman–Diaconis，用 IQR 代替标准差，对离群值稳健；
                 IQR=0（如大量重复值）时退化为 scott。
      auto    —— 默认规则：max(fd, sturges) 的 bin 数（与 numpy 的 'auto' 一致），
                 兼顾稳健性与分辨率；退化数据（全相同）返回 1。
    """
    n = len(data)
    if n == 0:
        raise ValueError("empty data")
    if n == 1:
        return 1
    lo, hi = min(data), max(data)
    if lo == hi:  # 全部相同取值：1 个 bin 即可
        return 1

    rule = rule.lower()
    if rule == "sqrt":
        return max(1, math.ceil(math.sqrt(n)))
    if rule == "sturges":
        return max(1, math.ceil(math.log2(n)) + 1)
    if rule == "rice":
        return max(1, math.ceil(2 * n ** (1.0 / 3.0)))
    if rule in ("scott", "fd", "auto"):
        s = _std(data)
        h_scott = 3.5 * s / n ** (1.0 / 3.0) if s > 0 else 0.0
        if rule == "scott":
            h = h_scott
        else:
            xs = sorted(data)
            iqr = _iqr(xs)
            h_fd = 2.0 * iqr / n ** (1.0 / 3.0) if iqr > 0 else 0.0
            if rule == "fd":
                h = h_fd if h_fd > 0 else h_scott
            else:  # auto = max(fd, sturges) 的 bin 数
                k_fd = math.ceil((hi - lo) / h_fd) if h_fd > 0 else 0
                k_sturges = math.ceil(math.log2(n)) + 1
                return max(1, k_fd, k_sturges)
        if h <= 0:
            return 1
        return max(1, math.ceil((hi - lo) / h))
    raise ValueError("unknown bin rule: %r" % rule)


def bin_edges(data, rule="auto", k=None):
    """返回 (edges, counts)。edges 长度 k+1，counts 长度 k。"""
    if k is None:
        k = auto_bin_count(data, rule)
    if k < 1:
        raise ValueError("k must be >= 1")
    lo, hi = min(data), max(data)
    if lo == hi:  # 退化：以该点为中心造一个单位宽度的 bin
        lo, hi = lo - 0.5, hi + 0.5
    width = (hi - lo) / k
    edges = [lo + i * width for i in range(k + 1)]
    edges[-1] = hi  # 消除浮点误差
    counts = [0] * k
    for x in data:
        idx = int((x - lo) / width)
        if idx >= k:  # 最大值归入最后一个 bin
            idx = k - 1
        elif idx < 0:
            idx = 0
        counts[idx] += 1
    return edges, counts


def histogram(data, rule="auto", k=None, density=True):
    """返回 (edges, heights)。density=True 时高度为密度（总面积=1），否则为计数。"""
    edges, counts = bin_edges(data, rule, k)
    n = len(data)
    if not density:
        return edges, [float(c) for c in counts]
    heights = []
    for i, c in enumerate(counts):
        w = edges[i + 1] - edges[i]
        heights.append(c / (n * w))
    return edges, heights


# ---------------------------------------------------------------- KDE

def silverman_bandwidth(data):
    """Silverman 经验法则带宽：h = 0.9 * min(std, IQR/1.34) * n^(-1/5)。

    用 min(std, IQR/1.34) 做稳健尺度：双峰/重尾时 IQR 项防止带宽过大。
    退化数据（尺度为 0，如单点或全部相同）回退到一个小的正带宽，
    保证 KDE 仍是良定义的归一化密度。
    """
    n = len(data)
    if n == 0:
        raise ValueError("empty data")
    xs = sorted(data)
    s = _std(data)
    iqr_sigma = _iqr(xs) / 1.34
    scale = min(s, iqr_sigma) if iqr_sigma > 0 else s
    if scale <= 0:
        # 全部相同：以 1.0（或数据量级）为尺度的回退带宽
        scale = max(abs(xs[0]), 1.0) * 1e-3
    return 0.9 * scale * n ** (-0.2)


def _gauss(u):
    return math.exp(-0.5 * u * u) / math.sqrt(2.0 * math.pi)


def kde(data, x, bandwidth=None):
    """在单点 x 处求 KDE 值（O(n)，适合少量查询点）。"""
    n = len(data)
    if n == 0:
        raise ValueError("empty data")
    h = bandwidth if bandwidth is not None else silverman_bandwidth(data)
    if h <= 0:
        raise ValueError("bandwidth must be positive")
    return sum(_gauss((x - xi) / h) for xi in data) / (n * h)


def kde_grid(data, bandwidth=None, grid_size=512, pad=4.0):
    """在等距网格上求 KDE（线性分箱加速，O(n + G^2)）。

    返回 (xs, ys)。网格覆盖 [min - pad*h, max + pad*h]，保证尾部质量
    可忽略，从而网格上的数值积分 ≈ 1。

    线性分箱：把每个样本按距离权重分摊到相邻两个网格点，再对网格权重
    与核函数做直接卷积。G=512 时约 26 万次核求值，纯 Python 亚秒级。

    为保证离散卷积逼近连续积分，网格步长自动加密到 <= h/2
    （卷积代价为 O(G * h/step)，与 G 无关，故加密很便宜）；
    网格点数上限 MAX_GRID，超出时（极端离群值把区间拉得极宽）
    按上限截断，此时积分可能偏离 1，属文档化的限制。
    """
    MAX_GRID = 1 << 20
    n = len(data)
    if n == 0:
        raise ValueError("empty data")
    h = bandwidth if bandwidth is not None else silverman_bandwidth(data)
    if h <= 0:
        raise ValueError("bandwidth must be positive")
    lo, hi = min(data), max(data)
    a, b = lo - pad * h, hi + pad * h
    g = max(grid_size, min(int(math.ceil((b - a) / (0.5 * h))) + 1, MAX_GRID), 2)
    step = (b - a) / (g - 1)

    # 线性分箱
    w = [0.0] * g
    for x in data:
        t = (x - a) / step
        i = int(t)
        if i < 0:
            i = 0
        if i >= g - 1:
            i = g - 2
        frac = t - i
        w[i] += 1.0 - frac
        w[i + 1] += frac

    # 核在网格上的取值（截断到 ±cutoff 个网格步，|u|>5 时高斯核 < 1e-5）
    cutoff = min(int(math.ceil(5.0 * h / step)), g - 1)
    kern = [_gauss(j * step / h) for j in range(cutoff + 1)]

    ys = [0.0] * g
    for i in range(g):
        acc = 0.0
        j_hi = min(i + cutoff, g - 1)
        j_lo = max(i - cutoff, 0)
        for j in range(j_lo, j_hi + 1):
            wj = w[j]
            if wj:
                acc += wj * kern[abs(i - j)]
        ys[i] = acc / (n * h)

    xs = [a + i * step for i in range(g)]
    return xs, ys


def integrate(xs, ys):
    """梯形法则数值积分。"""
    total = 0.0
    for i in range(len(xs) - 1):
        total += (ys[i] + ys[i + 1]) * 0.5 * (xs[i + 1] - xs[i])
    return total
