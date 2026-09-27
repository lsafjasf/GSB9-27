"""geolib 自测集：投影往返、大圆距离、球面面积与全部边界情形。

运行：python3 -m unittest test_geolib -v
"""

import math
import random
import unittest

from geolib import (
    MEAN_RADIUS,
    Equirectangular,
    CylindricalEqualArea,
    great_circle_distance,
    spherical_polygon_area,
    spherical_triangle_area,
    wrap_longitude,
    _to_vec,
    _signed_solid_angle,
)

R = MEAN_RADIUS
SPHERE_AREA = 4.0 * math.pi * R * R


def zone_area_exact(lat1, lat2, lon_span_deg):
    """纬带 [lat1,lat2] x 给定经度范围的精确球面面积（边沿纬线/经线）。"""
    return (R * R * math.radians(lon_span_deg)
            * abs(math.sin(math.radians(lat2)) - math.sin(math.radians(lat1))))


def densified_rect(lon1, lat1, lon2, lat2, step=1.0):
    """经纬矩形的边界点（纬线边按 step 度加密，使大圆边逼近纬线）。"""
    pts = []
    n = max(1, int(math.ceil((lon2 - lon1) / step)))
    for i in range(n + 1):
        pts.append((lon1 + (lon2 - lon1) * i / n, lat1))
    for i in range(n + 1):
        pts.append((lon2 - (lon2 - lon1) * i / n, lat2))
    return pts


def girard_area(coords):
    """独立算法：Girard 定理，累加各顶点有向内角求球面角盈。

    与库实现（扇形立体角剖分）完全不同的路径，用于交叉校验。
    """
    vecs = [_to_vec(lon, lat) for lon, lat in coords]
    m = len(vecs)
    if m < 3:
        return 0.0
    angle_sum = 0.0
    for i in range(m):
        b = vecs[i]
        a = vecs[i - 1]
        c = vecs[(i + 1) % m]
        ua = tuple(a[k] - sum(a[j] * b[j] for j in range(3)) * b[k]
                   for k in range(3))
        uc = tuple(c[k] - sum(c[j] * b[j] for j in range(3)) * b[k]
                   for k in range(3))
        na = math.sqrt(sum(v * v for v in ua))
        nc = math.sqrt(sum(v * v for v in uc))
        if na == 0.0 or nc == 0.0:
            continue
        cross = (ua[1] * uc[2] - ua[2] * uc[1],
                 ua[2] * uc[0] - ua[0] * uc[2],
                 ua[0] * uc[1] - ua[1] * uc[0])
        sin_t = -sum(cross[k] * b[k] for k in range(3)) / (na * nc)
        cos_t = sum(ua[k] * uc[k] for k in range(3)) / (na * nc)
        angle_sum += math.atan2(sin_t, cos_t)
    excess = angle_sum - (m - 2) * math.pi
    # 立体角按 mod 2*pi 归一到 (-pi, pi]，与「取较小侧」约定一致。
    excess = (excess + math.pi) % (2.0 * math.pi) - math.pi
    return abs(excess) * R * R


class TestProjectionRoundTrip(unittest.TestCase):
    """往返误差量化：全网格 + 2 万随机点，断言最大误差上界。"""

    def _check_roundtrip(self, proj):
        max_err_m = 0.0
        rng = random.Random(42)
        samples = [(rng.uniform(-180, 180), rng.uniform(-90, 90))
                   for _ in range(20000)]
        samples += [(lon, lat)
                    for lon in range(-180, 181, 15)
                    for lat in range(-90, 91, 15)]
        for lon, lat in samples:
            x, y = proj.forward(lon, lat)
            lon2, lat2 = proj.inverse(x, y)
            # 地理坐标意义下的往返误差（经差取短边，避免图幅切割线干扰）。
            dlon = (lon2 - lon + 180.0) % 360.0 - 180.0
            err = R * math.hypot(math.radians(dlon) * math.cos(math.radians(lat)),
                                 math.radians(lat2 - lat))
            max_err_m = max(max_err_m, err)
        return max_err_m

    def test_equirectangular_roundtrip(self):
        err = self._check_roundtrip(Equirectangular())
        self.assertLess(err, 1e-6)
        print(f"\n[量化] 等距圆柱往返最大误差: {err:.3e} m (2 万随机点+网格)")

    def test_equirectangular_roundtrip_with_standard_parallel(self):
        err = self._check_roundtrip(Equirectangular(lon0=120.0, lat1=35.0))
        self.assertLess(err, 1e-6)
        print(f"\n[量化] 等距圆柱(lon0=120,lat1=35)往返最大误差: {err:.3e} m")

    def test_equal_area_roundtrip(self):
        err = self._check_roundtrip(CylindricalEqualArea())
        # 极区 asin 病态放大浮点抖动，往返误差放宽到亚毫米级。
        self.assertLess(err, 1e-3)
        print(f"\n[量化] 等积圆柱往返最大误差: {err:.3e} m (2 万随机点+网格)")

    def test_equal_area_roundtrip_gall_peters(self):
        err = self._check_roundtrip(CylindricalEqualArea(lat1=45.0))
        self.assertLess(err, 1e-3)
        print(f"\n[量化] 等积圆柱(lat1=45)往返最大误差: {err:.3e} m")

    def test_poles_roundtrip_exact(self):
        # 极点在两种投影下都必须可逆（等积投影极点 y 有限）。
        for proj in (Equirectangular(), CylindricalEqualArea()):
            for lat in (-90.0, 90.0):
                x, y = proj.forward(30.0, lat)
                _, lat2 = proj.inverse(x, y)
                self.assertAlmostEqual(lat2, lat, places=9)

    def test_antimeridian_forward_continuity(self):
        # 中央经线取 180 时，179.9 与 -179.9 投影后 x 必须相邻（走短边），
        # 而不是分列图幅两端。
        for proj in (Equirectangular(lon0=180.0),
                     CylindricalEqualArea(lon0=180.0)):
            x1, _ = proj.forward(179.9, 0.0)
            x2, _ = proj.forward(-179.9, 0.0)
            gap = abs(x2 - x1)
            expected = R * math.radians(0.2)
            self.assertAlmostEqual(gap, expected, delta=expected * 1e-9)

    def test_invalid_params(self):
        with self.assertRaises(ValueError):
            Equirectangular(lat1=91.0)
        with self.assertRaises(ValueError):
            CylindricalEqualArea(lat1=90.0)
        with self.assertRaises(ValueError):
            Equirectangular().inverse(0.0, R * math.radians(91.0))


class TestGreatCircleDistance(unittest.TestCase):
    def test_zero_length_line(self):
        self.assertEqual(great_circle_distance(120.0, 30.0, 120.0, 30.0), 0.0)

    def test_known_quarter_meridian(self):
        d = great_circle_distance(0.0, 0.0, 0.0, 90.0)
        self.assertAlmostEqual(d, math.pi * R / 2.0, delta=1e-6)

    def test_antipodal_no_detour(self):
        # 对跖点距离必须恰为半周长 pi*R，不能绕远路或发散。
        d = great_circle_distance(0.0, 0.0, 180.0, 0.0)
        self.assertAlmostEqual(d, math.pi * R, delta=1e-6)
        d2 = great_circle_distance(10.0, 80.0, -170.0, -80.0)
        self.assertAlmostEqual(d2, math.pi * R, delta=1e-4)

    def test_antimeridian_short_way(self):
        # 跨日期变更线：179.9 -> -179.9 只有 0.2°，不得按 359.8° 绕远。
        d = great_circle_distance(179.9, 0.0, -179.9, 0.0)
        expected = R * math.radians(0.2)
        self.assertAlmostEqual(d, expected, delta=expected * 1e-9)
        self.assertLess(d, 25000.0)  # ~22.2 km

    def test_symmetry_and_nonnegative(self):
        rng = random.Random(7)
        for _ in range(1000):
            a = (rng.uniform(-180, 180), rng.uniform(-90, 90))
            b = (rng.uniform(-180, 180), rng.uniform(-90, 90))
            d_ab = great_circle_distance(*a, *b)
            d_ba = great_circle_distance(*b, *a)
            self.assertGreaterEqual(d_ab, 0.0)
            self.assertAlmostEqual(d_ab, d_ba, places=6)
            self.assertLessEqual(d_ab, math.pi * R + 1e-6)


class TestSphericalPolygonArea(unittest.TestCase):
    def test_single_point_and_line_are_zero(self):
        self.assertEqual(spherical_polygon_area([]), 0.0)
        self.assertEqual(spherical_polygon_area([(10.0, 20.0)]), 0.0)
        # 零长度线（两点重合）与两点折线面积均为 0。
        self.assertEqual(spherical_polygon_area([(10.0, 20.0), (10.0, 20.0)]), 0.0)
        self.assertEqual(spherical_polygon_area([(0.0, 0.0), (10.0, 0.0)]), 0.0)
        # 重复点构成的退化环面积也为 0。
        dup = [(5.0, 5.0)] * 6
        self.assertEqual(spherical_polygon_area(dup), 0.0)

    def test_octant_exact(self):
        # 三条互相垂直的大圆围成的八分球面，解析值 = 4*pi*R^2/8。
        poly = [(0, 0), (90, 0), (0, 90)]
        self.assertAlmostEqual(spherical_polygon_area(poly),
                               SPHERE_AREA / 8.0,
                               delta=SPHERE_AREA * 1e-12)

    def test_hemisphere_exact(self):
        # 赤道环（加密）围成的北半球，解析值 = 2*pi*R^2。
        poly = [(lon, 0.0) for lon in range(-180, 180, 10)]
        self.assertAlmostEqual(spherical_polygon_area(poly),
                               0.5 * SPHERE_AREA,
                               delta=SPHERE_AREA * 1e-12)

    def test_equator_zone_densified(self):
        # 赤道附近 20°x10° 经纬格（纬线加密到 0.5°），对照纬带解析式。
        poly = densified_rect(-10, 0, 10, 10, step=0.5)
        expected = zone_area_exact(0, 10, 20)
        self.assertAlmostEqual(spherical_polygon_area(poly), expected,
                               delta=expected * 1e-4)

    def test_polar_cap(self):
        # 极冠：89° 纬线环（每 10° 加密），面积应接近 2*pi*R^2*(1-cos1°)，
        # 且必须取「小」的那一侧（不能返回几乎整个半球）。
        poly = [(lon, 89.0) for lon in range(-180, 180, 1)]
        area = spherical_polygon_area(poly)
        expected = 2.0 * math.pi * R * R * (1.0 - math.cos(math.radians(1.0)))
        self.assertAlmostEqual(area, expected, delta=expected * 1e-3)
        self.assertLess(area, 0.01 * SPHERE_AREA)

    def test_near_pole_small_cell(self):
        # 极点附近 1°x0.1° 小格（纬线加密），对照纬带解析式，必须为正。
        poly = densified_rect(10, 89.9, 11, 90.0, step=0.1)
        area = spherical_polygon_area(poly)
        self.assertGreater(area, 0.0)
        self.assertAlmostEqual(area, zone_area_exact(89.9, 90.0, 1),
                               delta=zone_area_exact(89.9, 90.0, 1) * 1e-4)

    def test_antimeridian_crossing(self):
        # 同一多边形平移过 ±180°：面积必须一致，且走短边。
        poly_a = [(170, 0), (-170, 0), (-170, 10), (170, 10)]
        poly_b = [(170, 0), (190, 0), (190, 10), (170, 10)]  # 190 == -170
        area_a = spherical_polygon_area(poly_a)
        area_b = spherical_polygon_area(poly_b)
        self.assertAlmostEqual(area_a, area_b, delta=1.0)
        # 与独立算法（扇形剖分 + 有向立体角）交叉验证。
        self.assertAlmostEqual(area_a, girard_area(poly_a),
                               delta=1.0)
        # 与加密版（大圆边逼近纬线）的面积粗一致（弦/纬线偏差 < 2%）。
        pts = [(170 + i, 0.0) for i in range(0, 21)]
        pts += [(190 - i, 10.0) for i in range(0, 21)]
        pts = [(wrap_longitude(lon), lat) for lon, lat in pts]
        self.assertAlmostEqual(area_a, spherical_polygon_area(pts),
                               delta=area_a * 0.02)

    def test_antimeridian_densified(self):
        # 跨变更线的加密纬带：170 -> -170（即 190），对照解析式。
        pts = [(170 + i, 0.0) for i in range(0, 21)]
        pts += [(190 - i, 10.0) for i in range(0, 21)]
        pts = [(wrap_longitude(lon), lat) for lon, lat in pts]
        expected = zone_area_exact(0, 10, 20)
        self.assertAlmostEqual(spherical_polygon_area(pts), expected,
                               delta=expected * 1e-4)

    def test_hemisphere_crossing_polygon(self):
        # 跨南北半球、跨 60° 经度（纬线加密），对照纬带解析式。
        poly = densified_rect(-30, -45, 30, 45, step=0.5)
        expected = zone_area_exact(-45, 45, 60)
        self.assertAlmostEqual(spherical_polygon_area(poly), expected,
                               delta=expected * 1e-4)

    def test_full_longitude_band(self):
        # 绕全球一整圈的纬带（跨所有半球），纬线每 1° 加密。
        pts = [(lon, -20.0) for lon in range(-180, 181, 1)]
        pts += [(lon, 20.0) for lon in range(180, -181, -1)]
        expected = zone_area_exact(-20, 20, 360)
        self.assertAlmostEqual(spherical_polygon_area(pts), expected,
                               delta=expected * 1e-4)

    def test_larger_than_hemisphere_returns_complement(self):
        # ±60° 全球纬带面积 > 半球：按约定返回补集（南北两个极冠）。
        pts = [(lon, -60.0) for lon in range(-180, 181, 1)]
        pts += [(lon, 60.0) for lon in range(180, -181, -1)]
        area = spherical_polygon_area(pts)
        caps = SPHERE_AREA - zone_area_exact(-60, 60, 360)
        self.assertAlmostEqual(area, caps, delta=caps * 1e-3)
        self.assertLess(area, 0.5 * SPHERE_AREA)

    def test_no_negative_area_and_orientation_independent(self):
        rng = random.Random(3)
        for _ in range(500):
            n = rng.randint(3, 12)
            lon_c = rng.uniform(-180, 180)
            lat_c = rng.uniform(-80, 80)
            poly = [(wrap_longitude(lon_c + rng.uniform(-5, 5)),
                     max(-89.9, min(89.9, lat_c + rng.uniform(-5, 5))))
                    for _ in range(n)]
            area = spherical_polygon_area(poly)
            self.assertGreaterEqual(area, 0.0)
            self.assertLessEqual(area, 0.5 * SPHERE_AREA + 1e-3)
            # 顶点逆序（走向翻转）面积不变。
            self.assertAlmostEqual(area, spherical_polygon_area(poly[::-1]),
                                   delta=max(1e-6, area * 1e-9))

    def test_cross_validate_with_solid_angle(self):
        # 独立算法交叉验证：扇形三角剖分 + 有向立体角求和。
        rng = random.Random(11)
        for _ in range(300):
            n = rng.randint(3, 8)
            lon_c = rng.uniform(-170, 170)
            lat_c = rng.uniform(-60, 60)
            poly = [(lon_c + rng.uniform(-3, 3), lat_c + rng.uniform(-3, 3))
                    for _ in range(n)]
            area = spherical_polygon_area(poly)
            self.assertAlmostEqual(area, girard_area(poly),
                                   delta=max(1e-3, area * 1e-9))

    def test_triangle_area_known(self):
        area = spherical_triangle_area((0, 0), (90, 0), (0, 90))
        self.assertAlmostEqual(area, SPHERE_AREA / 8.0,
                               delta=SPHERE_AREA * 1e-12)
        # 退化三角形（共线/重复点）面积为 0。
        self.assertEqual(spherical_triangle_area((0, 0), (0, 0), (1, 1)), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
