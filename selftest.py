"""selftest.py — density.py 的自测与基准。运行: python3 selftest.py"""

import math
import random
import time

from density import (
    BIN_RULES,
    histogram,
    integrate_trapezoid,
    kde,
    silverman_bandwidth,
)

PASS, FAIL = "PASS", "FAIL"
_failures = []


def check(name, cond, detail=""):
    tag = PASS if cond else FAIL
    if not cond:
        _failures.append(name)
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))


def section(title):
    print(f"\n== {title} ==")


# ---------------------------------------------------------------- 数据生成

def gen_normal(n, mu=0.0, sigma=1.0, seed=42):
    rng = random.Random(seed)
    return [rng.gauss(mu, sigma) for _ in range(n)]


def gen_bimodal(n, seed=42):
    rng = random.Random(seed)
    return [rng.gauss(-2.5, 0.6) if rng.random() < 0.5 else rng.gauss(2.5, 0.6)
            for _ in range(n)]


def gen_heavy_tail(n, seed=42):
    """柯西分布（均值/方差都不存在）的极端长尾样本。"""
    rng = random.Random(seed)
    return [math.tan(math.pi * (rng.random() - 0.5)) for _ in range(n)]


def count_modes(xs, ys, min_prom_ratio=0.1):
    """数局部峰（要求显著性：峰值高于两侧邻域最低点的 min_prom_ratio）。"""
    peaks = []
    for i in range(1, len(ys) - 1):
        if ys[i] > ys[i - 1] and ys[i] >= ys[i + 1]:
            peaks.append(i)
    if not peaks:
        return 0
    ymax = max(ys)
    return sum(1 for i in peaks if ys[i] > min_prom_ratio * ymax)


def roughness(xs, ys):
    """光滑度度量 R(f'') = ∫f''² 的离散近似，越大越粗糙。"""
    s = 0.0
    for i in range(1, len(ys) - 1):
        dx = (xs[i + 1] - xs[i - 1]) / 2
        d2 = (ys[i + 1] - 2 * ys[i] + ys[i - 1]) / (dx * dx)
        s += d2 * d2 * dx
    return s


# ---------------------------------------------------------------- 1. 直方图

def test_histogram():
    section("直方图：分箱规则与归一化")
    data = gen_normal(1000)
    for rule in BIN_RULES:
        edges, counts, dens = histogram(data, bins=rule)
        area = sum(d * (edges[i + 1] - edges[i]) for i, d in enumerate(dens))
        check(f"规则 {rule:8s} 计数守恒且密度积分=1",
              sum(counts) == 1000 and abs(area - 1.0) < 1e-9,
              f"bins={len(counts)}, area={area:.12f}")
    edges, counts, _ = histogram(data, bins=7)
    check("int 箱数生效", len(counts) == 7)
    edges, counts, _ = histogram(data, bins=[-4, -1, 0, 1, 4])
    check("自定义边界生效", len(counts) == 4 and sum(counts) <= 1000)
    try:
        histogram([], bins="auto")
        check("空数据抛 ValueError", False)
    except ValueError:
        check("空数据抛 ValueError", True)


# ---------------------------------------------------------------- 2. KDE 正确性与归一化

def test_kde_normalization():
    section("KDE：归一化数值验证（梯形积分 ≈ 1）")
    cases = {
        "正态 N(0,1) n=2000": gen_normal(2000),
        "双峰 n=2000": gen_bimodal(2000),
        "长尾(柯西) n=2000": gen_heavy_tail(2000),
        "单点 [3.14]": [3.14],
        "全部同值 7×100": [7.0] * 100,
    }
    for name, data in cases.items():
        xs, ys, h = kde(data)
        integ = integrate_trapezoid(xs, ys)
        check(f"{name:22s} ∫f̂dx == 1", abs(integ - 1.0) < 1e-9,
              f"h={h:.4g}, integral={integ:.15f}")


def test_kde_accuracy():
    section("KDE：估计精度（与真值/结构对比）")
    data = gen_normal(5000)
    xs, ys, h = kde(data)
    # 与标准正态密度的 L1 误差应较小
    l1 = sum(abs(y - math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi))
             for x, y in zip(xs, ys)) * (xs[1] - xs[0])
    check("正态 KDE 的 L1 误差 < 0.05", l1 < 0.05, f"L1={l1:.4f}, h={h:.4f}")

    data = gen_bimodal(4000)
    xs, ys, h = kde(data)
    modes = count_modes(xs, ys)
    check("双峰分布 KDE 呈现 2 个峰", modes == 2, f"modes={modes}, h={h:.4f}")
    peak_x = xs[ys.index(max(ys))]
    check("峰位接近 ±2.5", abs(abs(peak_x) - 2.5) < 0.5, f"peak at {peak_x:.2f}")


def test_edge_cases():
    section("边界用例")
    xs, ys, h = kde([3.14])
    check("单点样本：不崩溃且积分=1",
          abs(integrate_trapezoid(xs, ys) - 1) < 1e-9, f"h={h:.4g}")
    xs, ys, h = kde([7.0] * 100)
    peak_x = xs[ys.index(max(ys))]
    check("全部同值：峰位在 7 附近", abs(peak_x - 7.0) < 0.2,
          f"h={h:.4g}, peak={peak_x:.3f}")
    edges, counts, dens = histogram([5.0] * 50)
    area = sum(d * (edges[i + 1] - edges[i]) for i, d in enumerate(dens))
    check("全部同值直方图：积分=1", abs(area - 1) < 1e-9 and sum(counts) == 50)
    data = gen_heavy_tail(5000)
    xs, ys, h = kde(data)
    check("极端长尾(柯西)：有限且积分=1",
          all(math.isfinite(y) for y in ys)
          and abs(integrate_trapezoid(xs, ys) - 1) < 1e-9,
          f"h={h:.4g}, max|x|={max(abs(v) for v in data):.3g}")
    try:
        kde([1.0, 2.0], bandwidth=0)
        check("非正带宽抛 ValueError", False)
    except ValueError:
        check("非正带宽抛 ValueError", True)


# ---------------------------------------------------------------- 3. 带宽敏感性

def sensitivity_report():
    section("带宽敏感性（双峰数据 n=4000，h = 系数 × Silverman）")
    data = gen_bimodal(4000)
    h0 = silverman_bandwidth(data)
    print(f"  Silverman 自动带宽 h0 = {h0:.4f}")
    print(f"  {'系数':>6} {'带宽h':>8} {'粗糙度R(f\")':>14} {'峰数':>4} {'峰间距':>7}")
    for mult in (0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
        xs, ys, h = kde(data, bandwidth=h0 * mult)
        r = roughness(xs, ys)
        m = count_modes(xs, ys)
        peaks = [i for i in range(1, len(ys) - 1)
                 if ys[i] > ys[i - 1] and ys[i] >= ys[i + 1]
                 and ys[i] > 0.1 * max(ys)]
        gap = (xs[max(peaks)] - xs[min(peaks)]) if len(peaks) >= 2 else float("nan")
        print(f"  {mult:>6.2f} {h:>8.4f} {r:>14.4f} {m:>4d} {gap:>7.2f}")
    print("  解读：h 偏小 → 粗糙度飙升、出现假峰(0.25x 时 3 峰)；"
          "h 偏大 → 过度平滑，8x 时双峰被抹成单峰；0.5x~2x 区间峰数稳定为 2。")


# ---------------------------------------------------------------- 4. 性能

def benchmark():
    section("性能基准：n = 100,000")
    rng = random.Random(7)
    data = [rng.gauss(0, 1) if rng.random() < 0.7 else rng.gauss(5, 2)
            for _ in range(100_000)]

    t0 = time.perf_counter()
    edges, counts, _ = histogram(data, bins="auto")
    t_hist = time.perf_counter() - t0
    print(f"  直方图(auto, {len(counts)} bins): {t_hist*1000:8.1f} ms")

    t0 = time.perf_counter()
    h = silverman_bandwidth(data)
    t_bw = time.perf_counter() - t0
    print(f"  Silverman 带宽 h={h:.4f}:        {t_bw*1000:8.1f} ms")

    t0 = time.perf_counter()
    xs, ys, h = kde(data)
    t_kde = time.perf_counter() - t0
    integ = integrate_trapezoid(xs, ys)
    print(f"  KDE(分箱快速算法, 512 网格): {t_kde*1000:8.1f} ms, integral={integ:.12f}")
    check("10 万点 KDE 在 5 秒内完成", t_kde < 5.0, f"{t_kde:.2f}s")
    check("10 万点 KDE 积分=1", abs(integ - 1) < 1e-9)


if __name__ == "__main__":
    test_histogram()
    test_kde_normalization()
    test_kde_accuracy()
    test_edge_cases()
    sensitivity_report()
    benchmark()
    print(f"\n{'全部通过' if not _failures else f'失败 {len(_failures)} 项: {_failures}'}")
    raise SystemExit(1 if _failures else 0)
