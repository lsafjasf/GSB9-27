"""误差对比：球面精确值 vs 平面近似，按纬度给出误差数据。

对比对象：
- 距离：大圆距离（真值） vs 两种平面近似
    A. 朴素平面：经纬度直接当平面坐标（1° = 111.195 km，两方向同尺度）
    B. 等距圆柱修正：dx = R*cos(φ)*Δλ，dy = R*Δφ（局部正切近似）
- 面积：球面多边形面积（真值，边界加密使大圆边逼近纬线） vs
    A. 朴素平面：度² -> (111.195 km)²
    B. 等距圆柱投影后鞋带公式

运行：python3 compare_errors.py
"""

import math

from geolib import MEAN_RADIUS as R, great_circle_distance, spherical_polygon_area

M_PER_DEG = math.pi * R / 180.0  # 111195 m/deg（子午线方向）

LATS = [0, 15, 30, 45, 60, 75, 85]


def planar_dist_naive(dlon, dlat):
    return M_PER_DEG * math.hypot(dlon, dlat)


def planar_dist_equirect(dlon, dlat, lat):
    dx = R * math.radians(dlon) * math.cos(math.radians(lat))
    dy = R * math.radians(dlat)
    return math.hypot(dx, dy)


def densified_cell(lon1, lat1, lon2, lat2, step=0.1):
    pts = []
    n = int(math.ceil((lon2 - lon1) / step))
    for i in range(n + 1):
        pts.append((lon1 + (lon2 - lon1) * i / n, lat1))
    for i in range(n + 1):
        pts.append((lon2 - (lon2 - lon1) * i / n, lat2))
    return pts


def planar_area_naive(dlon, dlat):
    return (M_PER_DEG * dlon) * (M_PER_DEG * dlat)


def planar_area_equirect(dlon, dlat, lat):
    return (R * math.radians(dlon) * math.cos(math.radians(lat))
            ) * (R * math.radians(dlat))


def rel_err(approx, exact):
    return (approx - exact) / exact * 100.0


def main():
    print("=" * 88)
    print("表 1：距离误差（对角线 Δlon=Δlat=1°，真值 = 大圆距离）")
    print("=" * 88)
    print(f"{'纬度':>4} {'大圆距离(km)':>12} {'朴素平面(km)':>12} {'误差%':>9} "
          f"{'等距圆柱(km)':>12} {'误差%':>9}")
    for lat in LATS:
        exact = great_circle_distance(0, lat, 1, lat + 1)
        naive = planar_dist_naive(1, 1)
        equi = planar_dist_equirect(1, 1, lat + 0.5)
        print(f"{lat:>4} {exact/1000:>12.3f} {naive/1000:>12.3f} "
              f"{rel_err(naive, exact):>9.3f} {equi/1000:>12.3f} "
              f"{rel_err(equi, exact):>9.4f}")

    print()
    print("=" * 88)
    print("表 2：距离误差（长对角线 Δlon=Δlat=10°，真值 = 大圆距离）")
    print("=" * 88)
    print(f"{'纬度':>4} {'大圆距离(km)':>12} {'朴素平面(km)':>12} {'误差%':>9} "
          f"{'等距圆柱(km)':>12} {'误差%':>9}")
    for lat in LATS:
        if lat + 10 > 89:
            continue
        exact = great_circle_distance(0, lat, 10, lat + 10)
        naive = planar_dist_naive(10, 10)
        equi = planar_dist_equirect(10, 10, lat + 5)
        print(f"{lat:>4} {exact/1000:>12.3f} {naive/1000:>12.3f} "
              f"{rel_err(naive, exact):>9.3f} {equi/1000:>12.3f} "
              f"{rel_err(equi, exact):>9.4f}")

    print()
    print("=" * 88)
    print("表 3：面积误差（1°x1° 经纬格，真值 = 球面多边形面积）")
    print("=" * 88)
    print(f"{'纬度':>4} {'球面面积(km²)':>14} {'朴素平面(km²)':>14} {'误差%':>9} "
          f"{'等距圆柱(km²)':>14} {'误差%':>9}")
    for lat in LATS:
        if lat + 1 > 90:
            continue
        cell = densified_cell(0, lat, 1, lat + 1)
        exact = spherical_polygon_area(cell)
        naive = planar_area_naive(1, 1)
        equi = planar_area_equirect(1, 1, lat + 0.5)
        print(f"{lat:>4} {exact/1e6:>14.3f} {naive/1e6:>14.3f} "
              f"{rel_err(naive, exact):>9.3f} {equi/1e6:>14.3f} "
              f"{rel_err(equi, exact):>9.4f}")

    print()
    print("=" * 88)
    print("表 4：面积误差（10°x10° 经纬格，真值 = 球面多边形面积）")
    print("=" * 88)
    print(f"{'纬度':>4} {'球面面积(km²)':>14} {'朴素平面(km²)':>14} {'误差%':>9} "
          f"{'等距圆柱(km²)':>14} {'误差%':>9}")
    for lat in LATS:
        if lat + 10 > 90:
            continue
        cell = densified_cell(0, lat, 10, lat + 10)
        exact = spherical_polygon_area(cell)
        naive = planar_area_naive(10, 10)
        equi = planar_area_equirect(10, 10, lat + 5)
        print(f"{lat:>4} {exact/1e6:>14.3f} {naive/1e6:>14.3f} "
              f"{rel_err(naive, exact):>9.3f} {equi/1e6:>14.3f} "
              f"{rel_err(equi, exact):>9.4f}")


if __name__ == "__main__":
    main()
