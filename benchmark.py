#!/usr/bin/env python3
"""基准脚本：不同阈值下的压缩率与耗时，输出表格并写 compression_data.csv。"""
import csv
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from polyline_simplify import simplify, verify_analytic
from verify_deviation import gen_trajectory, gen_closed_trajectory


def run_case(name, pts, tolerances, closed, writer):
    print(f"\n=== {name} (n={len(pts)}, closed={closed}) ===")
    header = f"{'tol':>12} {'kept':>8} {'ratio':>8} {'time_ms':>10} " \
             f"{'max_dev':>12} {'bound_ok':>8} {'self_x':>6}"
    print(header)
    print("-" * len(header))
    for tol in tolerances:
        t0 = time.perf_counter()
        res = simplify(pts, tol)
        dt = (time.perf_counter() - t0) * 1000
        ok, dev, _ = verify_analytic(pts, res.indices, tol, res.closed, leaf=res.leaf)
        row = [name, len(pts), tol, len(res.indices),
               f"{res.compression_ratio:.6f}", f"{dt:.3f}",
               f"{dev:.6g}", ok, len(res.self_intersections)]
        writer.writerow(row)
        print(f"{tol:>12.6g} {len(res.indices):>8} "
              f"{res.compression_ratio:>8.4f} {dt:>10.2f} "
              f"{dev:>12.6g} {str(ok):>8} {len(res.self_intersections):>6}")


def main():
    out_path = os.path.join(os.path.dirname(__file__), "compression_data.csv")
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["case", "n", "tolerance", "kept", "ratio",
                         "time_ms", "max_dev", "bound_ok", "self_intersections"])

        tolerances = [0.0, 1e-4, 1e-3, 1e-2, 0.1, 0.5, 1.0, 5.0, 50.0, 1e9]

        # 开折线：2 万点噪声轨迹
        pts_open = gen_trajectory(20000, seed=42)
        run_case("open_noisy_20k", pts_open, tolerances, False, writer)

        # 闭合折线：带噪声椭圆环
        pts_closed = gen_closed_trajectory(20000, seed=42)
        run_case("closed_noisy_ellipse_20k", pts_closed, tolerances, True, writer)

        # 自交演示：螺旋线（半径 1->50，10 圈）在大阈值下简化会产生
        # 自交，应被检测并报告（self_x > 0）
        n_sp = 5000
        spiral = [((1 + 49 * i / n_sp) * math.cos(20 * math.pi * i / n_sp),
                   (1 + 49 * i / n_sp) * math.sin(20 * math.pi * i / n_sp))
                  for i in range(n_sp)]
        run_case("spiral_selfx_demo_5k", spiral, [2.0, 8.0, 20.0], False, writer)

        # 近似共线轨迹
        rng = random.Random(7)
        pts_collinear = [(i * 1.0, 2.0 * i + rng.gauss(0, 0.01))
                         for i in range(20000)]
        run_case("near_collinear_20k", pts_collinear, tolerances, False, writer)

    print(f"\n数据已写入 {out_path}")


if __name__ == "__main__":
    main()
