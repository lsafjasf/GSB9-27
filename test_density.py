"""test_density.py — 自测：正确性、归一化、边界用例、带宽敏感性、性能基准。

运行：python3 test_density.py
"""

import math
import random
import time

from density import (
    histogram, bin_edges, auto_bin_count,
    silverman_bandwidth, kde, kde_grid, integrate,
)


# ---------------------------------------------------------------- 数据生成

def sample_normal(n, mu=0.0, sigma=1.0, rng=None):
    rng = rng or random
    return [rng.gauss(mu, sigma) for _ in range(n)]


def sample_bimodal(n, rng=None):
    rng = rng or random
    return [rng.gauss(-2.0, 0.6) if rng.random() < 0.5 else rng.gauss(2.5, 0.9)
            for _ in range(n)]


def sample_cauchy(n, rng=None):
    """极端长尾：标准柯西分布。"""
    rng = rng or random
    return [math.tan(math.pi * (rng.random() - 0.5)) for _ in range(n)]


# ---------------------------------------------------------------- 工具

def count_modes(xs, ys):
    """数曲线的局部峰个数（用于带宽敏感性）。"""
    modes = 0
    for i in range(1, len(ys) - 1):
        if ys[i] > ys[i - 1] and ys[i] >= ys[i + 1] and ys[i] > 1e-6:
            modes += 1
    return modes


def roughness(xs, ys):
    """∫(f'')² 的数值近似：越大曲线越毛糙。"""
    total = 0.0
    for i in range(1, len(ys) - 1):
        h1 = xs[i] - xs[i - 1]
        h2 = xs[i + 1] - xs[i]
        d2 = 2.0 * ((ys[i + 1] - ys[i]) / h2 - (ys[i] - ys[i - 1]) / h1) / (h1 + h2)
        total += d2 * d2 * (h1 + h2) / 2.0
    return total


def check(name, cond):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", name))
    if not cond:
        check.failed += 1
check.failed = 0


# ---------------------------------------------------------------- 1. 直方图

def test_histogram():
    print("== 直方图 ==")
    rng = random.Random(42)
    data = sample_normal(1000, rng=rng)

    for rule in ("sqrt", "sturges", "rice", "scott", "fd", "auto"):
        edges, counts = bin_edges(data, rule)
        k = len(counts)
        check("rule=%-8s 计数总和 == n (k=%d)" % (rule, k), sum(counts) == len(data))
        check("rule=%-8s 边界单调递增" % rule,
              all(edges[i] < edges[i + 1] for i in range(k)))

    edges, heights = histogram(data, "auto", density=True)
    area = sum(heights[i] * (edges[i + 1] - edges[i]) for i in range(len(heights)))
    print("  密度直方图总面积 = %.10f" % area)
    check("密度直方图面积 == 1", abs(area - 1.0) < 1e-12)

    check("固定 k=7 生效", len(bin_edges(data, k=7)[1]) == 7)
    try:
        auto_bin_count(data, "bogus")
        check("未知规则抛异常", False)
    except ValueError:
        check("未知规则抛异常", True)


# ---------------------------------------------------------------- 2. 边界用例

def test_edge_cases():
    print("== 边界用例 ==")
    # 单点样本
    one = [3.14]
    check("单点：分箱数 == 1", auto_bin_count(one) == 1)
    edges, counts = bin_edges(one)
    check("单点：计数 == 1", sum(counts) == 1)
    h = silverman_bandwidth(one)
    xs, ys = kde_grid(one, pad=5.0)
    integ = integrate(xs, ys)
    print("  单点 KDE: h=%.3g, 积分=%.8f" % (h, integ))
    check("单点：KDE 带宽为正且积分≈1", h > 0 and abs(integ - 1) < 1e-5)
    check("单点：KDE 峰值在样本处", abs(xs[ys.index(max(ys))] - 3.14) < 0.05)

    # 全部相同取值
    same = [2.0] * 500
    check("全相同：分箱数 == 1", auto_bin_count(same) == 1)
    edges, heights = histogram(same)
    area = sum(heights[i] * (edges[i + 1] - edges[i]) for i in range(len(heights)))
    check("全相同：直方图面积 == 1", abs(area - 1.0) < 1e-12)
    xs, ys = kde_grid(same, pad=5.0)
    integ = integrate(xs, ys)
    print("  全相同 KDE: h=%.3g, 积分=%.8f" % (silverman_bandwidth(same), integ))
    check("全相同：KDE 积分≈1", abs(integ - 1) < 1e-5)

    # 空数据
    for fn in (lambda: auto_bin_count([]), lambda: kde([], 0.0),
               lambda: kde_grid([]), lambda: silverman_bandwidth([])):
        try:
            fn()
            check("空数据抛异常", False)
        except ValueError:
            pass
    check("空数据抛异常", True)


# ---------------------------------------------------------------- 3. KDE 归一化

def test_kde_normalization():
    print("== KDE 归一化验证（梯形积分） ==")
    rng = random.Random(7)
    cases = {
        "正态 N(0,1)   n=2000": sample_normal(2000, rng=rng),
        "双峰           n=2000": sample_bimodal(2000, rng=rng),
        "柯西(极端长尾) n=2000": sample_cauchy(2000, rng=rng),
    }
    for name, data in cases.items():
        h = silverman_bandwidth(data)
        xs, ys = kde_grid(data, grid_size=1024, pad=4.0)
        integ = integrate(xs, ys)
        print("  %s  h=%.4f  积分=%.8f" % (name, h, integ))
        check("%s 积分≈1" % name.strip(), abs(integ - 1.0) < 1e-4)

    # kde_grid 与逐点 kde 一致性
    data = sample_normal(300, rng=rng)
    h = silverman_bandwidth(data)
    xs, ys = kde_grid(data, bandwidth=h, grid_size=256)
    mid = len(xs) // 2
    direct = kde(data, xs[mid], bandwidth=h)
    rel = abs(direct - ys[mid]) / max(direct, 1e-12)
    print("  网格近似 vs 逐点精确: 相对误差=%.2e" % rel)
    check("线性分箱近似误差 < 1%", rel < 0.01)


# ---------------------------------------------------------------- 4. 带宽敏感性

def test_bandwidth_sensitivity():
    print("== 带宽敏感性（双峰样本 n=2000，带宽倍数 → 曲线形态） ==")
    rng = random.Random(11)
    data = sample_bimodal(2000, rng=rng)
    h0 = silverman_bandwidth(data)
    print("  Silverman 带宽 h0 = %.4f" % h0)
    print("  %-10s %-10s %-14s %-8s %s" % ("倍数", "带宽", "粗糙度∫f''²", "峰数", "解读"))
    notes = {0.25: "过拟合，伪峰多", 0.5: "偏毛糙", 1.0: "自动选择",
             2.0: "偏平滑", 4.0: "过度平滑，细节丢失"}
    for mult in (0.25, 0.5, 1.0, 2.0, 4.0):
        h = h0 * mult
        xs, ys = kde_grid(data, bandwidth=h, grid_size=1024)
        r = roughness(xs, ys)
        m = count_modes(xs, ys)
        print("  %-10s %-10.4f %-14.4g %-8d %s" %
              ("%gx" % mult, h, r, m, notes[mult]))


# ---------------------------------------------------------------- 5. 性能基准

def test_benchmark():
    print("== 性能基准（n=100,000） ==")
    rng = random.Random(99)
    data = sample_bimodal(100_000, rng=rng)

    t0 = time.perf_counter()
    h = silverman_bandwidth(data)
    t1 = time.perf_counter()
    xs, ys = kde_grid(data, bandwidth=h, grid_size=512)
    t2 = time.perf_counter()
    edges, counts = bin_edges(data, "auto")
    t3 = time.perf_counter()
    integ = integrate(xs, ys)

    print("  带宽选择:        %.3f s" % (t1 - t0))
    print("  KDE 网格(512点): %.3f s   (积分=%.8f)" % (t2 - t1, integ))
    print("  直方图(auto):    %.3f s   (k=%d)" % (t3 - t2, len(counts)))
    print("  合计:            %.3f s" % (t3 - t0))
    check("十万点 KDE 积分≈1", abs(integ - 1.0) < 1e-4)


if __name__ == "__main__":
    test_histogram()
    test_edge_cases()
    test_kde_normalization()
    test_bandwidth_sensitivity()
    test_benchmark()
    print()
    if check.failed:
        print("失败 %d 项" % check.failed)
        raise SystemExit(1)
    print("全部通过 ✔")
