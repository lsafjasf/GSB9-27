"""性能测试：百万点投影 / 逆投影 / 大圆距离 / 多边形面积耗时。

运行：python3 benchmark.py
"""

import math
import random
import time

from geolib import (
    Equirectangular,
    CylindricalEqualArea,
    great_circle_distance,
    spherical_polygon_area,
)

N = 1_000_000


def make_points(n):
    rng = random.Random(20260927)
    return [(rng.uniform(-180.0, 180.0), rng.uniform(-90.0, 90.0))
            for _ in range(n)]


def time_it(label, fn):
    start = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - start
    print(f"{label:<44} {elapsed:>8.3f} s  ({elapsed / N * 1e6:>7.3f} µs/点)")
    return result


def main():
    print(f"数据规模: {N:,} 点")
    print("-" * 70)
    pts = make_points(N)

    eq = Equirectangular()
    ea = CylindricalEqualArea()

    xy = time_it("等距圆柱 正投影 forward", lambda: [eq.forward(lo, la) for lo, la in pts])
    time_it("等距圆柱 逆投影 inverse", lambda: [eq.inverse(x, y) for x, y in xy])
    xy2 = time_it("等积圆柱 正投影 forward", lambda: [ea.forward(lo, la) for lo, la in pts])
    time_it("等积圆柱 逆投影 inverse", lambda: [ea.inverse(x, y) for x, y in xy2])
    time_it("大圆距离（相邻点对）",
            lambda: [great_circle_distance(a[0], a[1], b[0], b[1])
                     for a, b in zip(pts, pts[1:] + pts[:1])])

    # 多边形面积：顶点数从 1e3 到 1e6，验证 O(n) 与单点成本。
    print("-" * 70)
    for m in (1_000, 10_000, 100_000, 1_000_000):
        ring = [(360.0 * i / m - 180.0,
                 60.0 * math.sin(2.0 * math.pi * i / m)) for i in range(m)]
        start = time.perf_counter()
        area = spherical_polygon_area(ring)
        elapsed = time.perf_counter() - start
        print(f"球面多边形面积 {m:>9,} 顶点: {elapsed:7.3f} s "
              f"({elapsed / m * 1e6:6.2f} µs/顶点), 面积={area:.6e} m²")


if __name__ == "__main__":
    main()
