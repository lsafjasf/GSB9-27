"""误差与法线连续性评估：与真实解析曲面量化对比。

运行：python3 evaluate.py
输出：
  1) 三种典型地形（平缓 / 陡坡 / 混合）上两种插值方式的高度与法线误差；
  2) 跨单元边法线连续性（相邻面法线最大夹角）；
  3) 含平台与空洞地形的功能演示。
"""

import math
import random

from heightmap import Heightmap, Status


# ---------- 解析曲面（带解析梯度，作为"真实地形"） ----------

def gentle(x, y):
    """平缓起伏：幅 10 m，波长 250/200 m，最大坡度约 14 度。"""
    return 10.0 * math.sin(2 * math.pi * x / 250.0) * math.cos(2 * math.pi * y / 200.0)


def gentle_grad(x, y):
    a = 2 * math.pi / 250.0
    b = 2 * math.pi / 200.0
    return (
        10.0 * a * math.cos(a * x) * math.cos(b * y),
        -10.0 * b * math.sin(a * x) * math.sin(b * y),
    )


def steep(x, y):
    """陡坡：高 80 m、半径 15 m 的高斯山包，最大坡度约 73 度。"""
    r2 = (x - 128.0) ** 2 + (y - 128.0) ** 2
    return 80.0 * math.exp(-r2 / (2 * 15.0 ** 2))


def steep_grad(x, y):
    s2 = 15.0 ** 2
    z = steep(x, y)
    return (-z * (x - 128.0) / s2, -z * (y - 128.0) / s2)


def mixed(x, y):
    """混合：平缓波 + 陡坡山包。"""
    return gentle(x, y) + steep(x, y)


def mixed_grad(x, y):
    g1 = gentle_grad(x, y)
    g2 = steep_grad(x, y)
    return (g1[0] + g2[0], g1[1] + g2[1])


SURFACES = [
    ("平缓起伏", gentle, gentle_grad),
    ("陡坡山包", steep, steep_grad),
    ("混合地形", mixed, mixed_grad),
]

DOMAIN = 256.0   # 地形范围 [0, 256] m
CELL = 2.0       # 采样间距 2 m（129x129 节点）
SAMPLES = 20000  # 每种配置的随机评估点数


def build_heightmap(fn):
    n = int(DOMAIN / CELL) + 1
    grid = [[fn(i * CELL, j * CELL) for i in range(n)] for j in range(n)]
    return Heightmap(grid, cell_size=CELL)


def angle_deg(n1, n2):
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(n1, n2))))
    return math.degrees(math.acos(dot))


def true_normal(grad):
    gx, gy = grad
    inv = 1.0 / math.sqrt(gx * gx + gy * gy + 1.0)
    return (-gx * inv, -gy * inv, inv)


def evaluate_surface(name, fn, grad_fn):
    hm = build_heightmap(fn)
    rng = random.Random(20261004)
    rows = []
    for method in (Heightmap.BILINEAR, Heightmap.BICUBIC):
        h_errs, n_errs = [], []
        for _ in range(SAMPLES):
            x = rng.uniform(0.0, DOMAIN)
            y = rng.uniform(0.0, DOMAIN)
            r = hm.query(x, y, method)
            assert r.ok
            h_errs.append(abs(r.height - fn(x, y)))
            n_errs.append(angle_deg(r.normal, true_normal(grad_fn(x, y))))
        rms_h = math.sqrt(sum(e * e for e in h_errs) / len(h_errs))
        rms_n = math.sqrt(sum(e * e for e in n_errs) / len(n_errs))
        rows.append((method, max(h_errs), rms_h, max(n_errs), rms_n))
    print(f"\n[{name}]  网格 {hm.nx}x{hm.ny}  间距 {CELL} m  随机点 {SAMPLES}")
    print(f"  {'方法':<10}{'高度最大误差(m)':>14}{'高度RMS(m)':>12}"
          f"{'法线最大角误差(°)':>16}{'法线RMS角误差(°)':>16}")
    for method, hmax, hrms, nmax, nrms in rows:
        print(f"  {method:<10}{hmax:>14.5f}{hrms:>12.5f}{nmax:>16.5f}{nrms:>16.5f}")
    return hm


def continuity_check(hm):
    """跨单元边法线连续性：在每条内部单元边两侧 eps 处取法线，求夹角。"""
    eps = 1e-6 * hm.cell
    print(f"\n[法线连续性]  在 {hm.nx}x{hm.ny} 网格全部内部单元边两侧 ±{eps:.1e} m 处采样")
    print(f"  {'方法':<10}{'最大法线跳变(°)':>16}{'平均法线跳变(°)':>16}")
    for method in (Heightmap.BILINEAR, Heightmap.BICUBIC):
        worst = 0.0
        total = 0.0
        count = 0
        for k in range(1, hm.nx - 1):           # 竖直单元边 x = k*cell
            x = hm.ox + k * hm.cell
            for j in range(hm.ny - 1):
                y = hm.oy + (j + 0.5) * hm.cell
                n1 = hm.normal(x - eps, y, method)
                n2 = hm.normal(x + eps, y, method)
                if n1 and n2:
                    a = angle_deg(n1, n2)
                    worst = max(worst, a)
                    total += a
                    count += 1
        for k in range(1, hm.ny - 1):           # 水平单元边 y = k*cell
            y = hm.oy + k * hm.cell
            for i in range(hm.nx - 1):
                x = hm.ox + (i + 0.5) * hm.cell
                n1 = hm.normal(x, y - eps, method)
                n2 = hm.normal(x, y + eps, method)
                if n1 and n2:
                    a = angle_deg(n1, n2)
                    worst = max(worst, a)
                    total += a
                    count += 1
        print(f"  {method:<10}{worst:>16.3e}{total / count:>16.3e}")


def platform_hole_demo():
    """平台 + 空洞地形：演示查询结果的明确状态。"""
    n = 21
    grid = []
    for j in range(n):
        row = []
        for i in range(n):
            if 8 <= i <= 12 and 8 <= j <= 12:
                row.append(None)              # 空洞（无数据区）
            elif 3 <= i <= 6 and 3 <= j <= 6:
                row.append(20.0)              # 平台
            else:
                row.append(0.0)
        grid.append(row)
    hm = Heightmap(grid, cell_size=5.0)
    print("\n[平台 + 空洞地形]  平台高 20 m，中央 5x5 空洞")
    cases = [
        ("平台中心 (22.5, 22.5)", 22.5, 22.5),
        ("平台边缘过渡 (17.5, 22.5)", 17.5, 22.5),
        ("空洞中心 (50.0, 50.0)", 50.0, 50.0),
        ("空洞边缘 (42.5, 50.0)", 42.5, 50.0),
        ("平坦地面 (80.0, 80.0)", 80.0, 80.0),
        ("网格外 (-1.0, 0.0)", -1.0, 0.0),
    ]
    print(f"  {'位置':<24}{'方法':<10}{'状态':<14}{'高度':>10}{'坡度(°)':>10}  法线")
    for label, x, y in cases:
        for method in (Heightmap.BILINEAR, Heightmap.BICUBIC):
            r = hm.query(x, y, method)
            h = f"{r.height:.3f}" if r.height is not None else "-"
            s = f"{r.slope_deg:.2f}" if r.slope_deg is not None else "-"
            nrm = ("(" + ", ".join(f"{c:.3f}" for c in r.normal) + ")") if r.normal else "-"
            print(f"  {label:<24}{method:<10}{r.status.value:<14}{h:>10}{s:>10}  {nrm}")


def main():
    print("=" * 78)
    print("地形插值误差评估（与真实解析曲面量化对比）")
    print("=" * 78)
    hm = None
    for name, fn, grad_fn in SURFACES:
        h = evaluate_surface(name, fn, grad_fn)
        if name == "混合地形":
            hm = h
    print()
    print("=" * 78)
    print("法线连续性校验（跨单元边，混合地形网格）")
    print("=" * 78)
    continuity_check(hm)
    print()
    print("=" * 78)
    print("平台 / 空洞 / 边界 功能演示")
    print("=" * 78)
    platform_hole_demo()


if __name__ == "__main__":
    main()
