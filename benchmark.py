"""误差对比、边界用例与性能基准。

运行：python3 benchmark.py
输出：终端表格 + REPORT.md（误差对比数据 / 性能数据）
"""

import math
import os
import random
import time

from geo_proj import (
    EARTH_MEAN_RADIUS as R,
    Equirectangular,
    LambertCylindricalEqualArea,
    great_circle_distance,
    path_length,
    planar_polygon_area,
    roundtrip_errors,
    spherical_polygon_area,
)

N_POINTS = 1_000_000


def planar_distance(lat1, lon1, lat2, lon2, lat_ref):
    """等距圆柱平面近似：x = R Δλ cos φ_ref，y = R Δφ。"""
    dlon = ((lon2 - lon1 + 540.0) % 360.0 - 180.0)
    x = math.radians(dlon) * R * math.cos(math.radians(lat_ref))
    y = math.radians(lat2 - lat1) * R
    return math.hypot(x, y)


def bench_distance():
    rows = []
    for target_km in (100.0, 1000.0):
        for lat in (0, 20, 40, 60, 80):
            dlon = math.degrees(target_km * 1000.0 / (R * math.cos(math.radians(lat))))
            lon1, lon2 = 0.0, dlon
            true_d = great_circle_distance(lat, lon1, lat, lon2)
            planar_d = planar_distance(lat, lon1, lat, lon2, lat)
            err = planar_d - true_d
            rows.append((target_km, lat, true_d / 1000, planar_d / 1000,
                         err, err / true_d * 100.0))
    return rows


def bench_area():
    rows = []
    for side_deg in (1.0, 5.0, 10.0):
        for lat in (0, 20, 40, 60, 80):
            half = side_deg / 2
            poly = [(lat - half, 0), (lat - half, side_deg),
                    (lat + half, side_deg), (lat + half, 0)]
            sphere = spherical_polygon_area(poly)
            plane = planar_polygon_area(poly, lat_ref=lat)
            err = plane - sphere
            rows.append((side_deg, lat, sphere / 1e6, plane / 1e6,
                         err / 1e6, err / sphere * 100.0))
    return rows


def edge_cases():
    cases = []
    cases.append(("零长度线（重合两点）",
                  great_circle_distance(30, 120, 30, 120)))
    cases.append(("单点/空多边形面积",
                  spherical_polygon_area([(30, 120)])))
    cases.append(("跨日期变更线 1° 距离(km)",
                  great_circle_distance(0, 179.5, 0, -179.5) / 1000))
    cases.append(("跨日期变更线 10°x20° 多边形(km²)",
                  spherical_polygon_area([(10, 170), (10, -170),
                                          (20, -170), (20, 170)]) / 1e6))
    cases.append(("极点两点距离(km)", great_circle_distance(90, 0, 90, 120) / 1000))
    cases.append(("80°N 绕极三角形面积(km²)",
                  spherical_polygon_area([(80, 0), (80, 120), (80, -120)]) / 1e6))
    cases.append(("跨赤道 20°x30° 多边形(km²)",
                  spherical_polygon_area([(-10, 0), (-10, 30),
                                          (10, 30), (10, 0)]) / 1e6))
    cases.append(("反向环绕面积是否相同(km²)",
                  spherical_polygon_area(list(reversed(
                      [(0, 0), (0, 10), (10, 10), (10, 0)]))) / 1e6))
    return cases


def bench_projection(n=N_POINTS):
    rng = random.Random(42)
    lats = [rng.uniform(-89.5, 89.5) for _ in range(n)]
    lons = [rng.uniform(-180.0, 180.0) for _ in range(n)]
    out = []
    for proj in (Equirectangular(), LambertCylindricalEqualArea(),
                 LambertCylindricalEqualArea(lat_std=30)):
        t0 = time.perf_counter()
        xy = [proj.forward(lats[i], lons[i]) for i in range(n)]
        t_fwd = time.perf_counter() - t0

        t0 = time.perf_counter()
        ll = [proj.inverse(x, y) for x, y in xy]
        t_inv = time.perf_counter() - t0

        out.append((proj.name, getattr(proj, "lat_std", 0.0),
                    t_fwd, n / t_fwd, t_inv, n / t_inv, t_fwd + t_inv))
    return out, (lats, lons)


def fmt_table(headers, rows, fmts):
    lines = []
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    lines += [head, sep]
    for r in rows:
        lines.append("| " + " | ".join(f.format(v) for f, v in zip(fmts, r)) + " |")
    return "\n".join(lines)


def main():
    print("=" * 70)
    print("1) 投影往返误差（forward → inverse，全经纬网格 90×72 点）")
    print("=" * 70)
    rt_rows = []
    for proj in (Equirectangular(), Equirectangular(lat_std=30),
                 LambertCylindricalEqualArea(),
                 LambertCylindricalEqualArea(lat_std=45)):
        mx, mean, cnt = roundtrip_errors(proj,
                                         lats=range(-89, 90, 2),
                                         lons=range(-180, 180, 5))
        rt_rows.append((proj.name, getattr(proj, "lat_std", 0.0),
                        mx * 1000.0, mean * 1000.0, cnt))
        print(f"  {proj.name:42s} std={getattr(proj,'lat_std',0):>3}° "
              f"max={mx*1000:.4f} mm mean={mean*1000:.3e} mm n={cnt}")

    print()
    print("=" * 70)
    print("2) 距离误差：大圆(真实) vs 等距圆柱平面近似，东西向")
    print("=" * 70)
    dist_rows = bench_distance()
    print(f"{'目标km':>7} {'纬度':>5} {'大圆km':>12} {'平面km':>12} "
          f"{'误差m':>10} {'相对%':>10}")
    for tk, lat, td, pdv, err, rel in dist_rows:
        print(f"{tk:7.0f} {lat:5.0f} {td:12.4f} {pdv:12.4f} {err:10.3f} {rel:10.5f}")

    print()
    print("=" * 70)
    print("3) 面积误差：球面多边形 vs 平面近似（正方形）")
    print("=" * 70)
    area_rows = bench_area()
    print(f"{'边长°':>6} {'中心纬':>6} {'球面km²':>14} {'平面km²':>14} "
          f"{'误差km²':>10} {'相对%':>9}")
    for side, lat, sa, pa, err, rel in area_rows:
        print(f"{side:6.1f} {lat:6.0f} {sa:14.3f} {pa:14.3f} {err:10.3f} {rel:9.5f}")

    print()
    print("=" * 70)
    print("4) 边界用例（均有确定、非负结果）")
    print("=" * 70)
    edges = edge_cases()
    for name, value in edges:
        unit = name.split("(")[-1].rstrip(")") if "(" in name else ""
        print(f"  {name:38s} = {value:,.4f}")

    print()
    print("=" * 70)
    print(f"5) 性能：{N_POINTS:,} 个随机点投影（纯 Python，标准库）")
    print("=" * 70)
    perf, _ = bench_projection()
    print(f"{'投影':42s} {'标准纬':>5} {'正向s':>8} {'点/s':>12} "
          f"{'逆向s':>8} {'点/s':>12}")
    for name, std, tf, rf, ti, ri, tot in perf:
        print(f"{name:42s} {std:5.0f} {tf:8.3f} {rf:12,.0f} {ti:8.3f} {ri:12,.0f}")

    # ---- 写 REPORT.md ----
    here = os.path.dirname(os.path.abspath(__file__))
    lines = []
    lines.append("# 误差对比与性能数据\n")
    lines.append("球体半径 R = 6,371,008.8 m（IUGG 平均半径）。")
    lines.append("所有数字由 `python3 benchmark.py` 生成。\n")
    lines.append("## 投影往返误差（forward→inverse，90×72 经纬网格）\n")
    lines.append(fmt_table(
        ["投影", "标准纬线", "最大误差(mm)", "平均误差(mm)", "样本数"],
        rt_rows, ["{}", "{:.0f}°", "{:.3e}", "{:.2e}", "{}"]))
    lines.append("")
    lines.append("## 距离误差：大圆 vs 等距圆柱平面（东西向）\n")
    lines.append(fmt_table(
        ["目标距离(km)", "纬度", "大圆(km)", "平面(km)", "误差(m)", "相对(%)"],
        dist_rows, ["{:.0f}", "{:.0f}", "{:.4f}", "{:.4f}", "{:+.3f}", "{:+.5f}"]))
    lines.append("")
    lines.append("## 面积误差：球面多边形 vs 平面近似（正方形）\n")
    lines.append(fmt_table(
        ["边长", "中心纬度", "球面(km²)", "平面(km²)", "误差(km²)", "相对(%)"],
        area_rows, ["{:.0f}°", "{:.0f}°", "{:.3f}", "{:.3f}", "{:+.3f}", "{:+.5f}"]))
    lines.append("")
    lines.append("## 边界用例（确定、非负、不绕远路）\n")
    lines.append(fmt_table(["用例", "结果"], edges, ["{}", "{:,.4f}"]))
    lines.append("")
    lines.append(f"## 性能：{N_POINTS:,} 点投影（纯 Python 标准库）\n")
    lines.append(fmt_table(
        ["投影", "标准纬线", "正向(s)", "正向(点/s)", "逆向(s)", "逆向(点/s)", "合计(s)"],
        perf, ["{}", "{:.0f}°", "{:.3f}", "{:,.0f}", "{:.3f}", "{:,.0f}", "{:.3f}"]))
    lines.append("")
    with open(os.path.join(here, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n已写入 {os.path.join(here, 'REPORT.md')}")


if __name__ == "__main__":
    main()
