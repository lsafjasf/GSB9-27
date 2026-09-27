#!/usr/bin/env python3
"""偏差验证脚本：简化折线并用「解析 + 采样」两种方式验证偏差上界。

用法：
    python3 verify_deviation.py [--tol 0.5] [--n 20000] [--closed] [--seed 42]
"""
import argparse
import math
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from polyline_simplify import simplify, verify_analytic, verify_sampling


def gen_trajectory(n: int, seed: int = 42):
    """生成模拟轨迹：蜿蜒路线（x 单调前进）+ 测量噪声（类 GPS 轨迹）。"""
    rng = random.Random(seed)
    pts = []
    for i in range(n):
        x = i * 0.5
        y = 50.0 * math.sin(i / 800.0) + 20.0 * math.sin(i / 150.0)
        pts.append((x + rng.gauss(0, 0.1), y + rng.gauss(0, 0.15)))
    return pts


def gen_closed_trajectory(n: int, seed: int = 42):
    """生成闭合轨迹：带噪声的椭圆环（首尾点重合）。"""
    rng = random.Random(seed)
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 20000.0 * math.cos(t) + rng.gauss(0, 0.3)
        y = 10000.0 * math.sin(t) + rng.gauss(0, 0.3)
        pts.append((x, y))
    pts.append(pts[0])
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=0.5)
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--closed", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if args.closed:
        pts = gen_closed_trajectory(args.n, args.seed)
    else:
        pts = gen_trajectory(args.n, args.seed)

    res = simplify(pts, args.tol)
    print(f"原点数:        {len(pts)}")
    print(f"保留点数:      {len(res.indices)}  (压缩率 {res.compression_ratio:.4f})")
    print(f"闭合:          {res.closed}  起点保留: {res.indices[0] == 0}")
    print(f"决策次数:      {len(res.decisions)}")
    print(f"自交线段对数:  {len(res.self_intersections)}")
    if res.self_intersections:
        print(f"  !! 检测到自交: {res.self_intersections[:10]}")

    ok_a, dev_a, worst = verify_analytic(pts, res.indices, args.tol, res.closed, leaf=res.leaf)
    ok_s, dev_s, ns = verify_sampling(pts, res.indices, args.tol, res.closed, leaf=res.leaf)
    print(f"[解析验证] 最大偏差 = {dev_a:.6f} (worst idx={worst}) "
          f"<= tol {args.tol} ? {'PASS' if ok_a else 'FAIL'}")
    print(f"[采样验证] 最大偏差 = {dev_s:.6f} (采样 {ns} 点) "
          f"<= tol {args.tol} ? {'PASS' if ok_s else 'FAIL'}")

    if not (ok_a and ok_s) or res.self_intersections:
        sys.exit(1)
    print("偏差上界验证通过。")


if __name__ == "__main__":
    main()
