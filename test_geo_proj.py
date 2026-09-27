"""边界用例集：python3 -m unittest test_geo_proj -v"""

import math
import unittest

from geo_proj import (
    EARTH_MEAN_RADIUS as R,
    Equirectangular,
    LambertCylindricalEqualArea,
    clamp_lon,
    haversine,
    path_length,
    planar_polygon_area,
    roundtrip_errors,
    spherical_polygon_area,
)

KM2 = 1e6


def rect(lat1, lat2, lon1, lon2):
    """经纬度矩形（lon2 可小于 lon1，表示跨日期变更线）。"""
    return [(lat1, lon1), (lat1, lon2), (lat2, lon2), (lat2, lon1)]


def analytic_rect_area(lat1, lat2, lon_span_deg):
    """球面经纬矩形精确面积 R²·Δλ·(sinφ2−sinφ1)。"""
    return R * R * math.radians(lon_span_deg) * (
        math.sin(math.radians(lat2)) - math.sin(math.radians(lat1))
    )



def _to_unit(lat, lon):
    p, l = math.radians(lat), math.radians(lon)
    return (math.cos(p) * math.cos(l), math.cos(p) * math.sin(l), math.sin(p))


def reference_spherical_area(poly):
    """独立参照实现：Gauss–Bonnet 法——在每个顶点用入射/出射大圆弧
    的单位切向量求有向内角，E = Σα − (n−2)π。
    与被测的 van Oosterom–Strackee 盈角法相互独立。"""
    n = len(poly)
    if n < 3:
        return 0.0
    v = [_to_unit(lat, lon) for lat, lon in poly]

    def dot(a, b):
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0])

    def norm(x):
        d = math.sqrt(dot(x, x))
        return (x[0] / d, x[1] / d, x[2] / d)

    excess = -(n - 2) * math.pi
    for i in range(n):
        vi = v[i]
        vp = v[(i - 1) % n]
        vn = v[(i + 1) % n]
        arrive = norm((vi[0] - dot(vi, vp) * vp[0],
                       vi[1] - dot(vi, vp) * vp[1],
                       vi[2] - dot(vi, vp) * vp[2]))   # 沿入边指向顶点
        leave = norm((vn[0] - dot(vn, vi) * vi[0],
                      vn[1] - dot(vn, vi) * vi[1],
                      vn[2] - dot(vn, vi) * vi[2]))    # 沿出边离开顶点
        back = (-arrive[0], -arrive[1], -arrive[2])
        alpha = math.atan2(dot(cross(back, leave), vi), dot(back, leave))
        excess -= alpha
    area = abs(excess) * R * R
    hemi = 2 * math.pi * R * R
    return 2 * hemi - area if area > hemi else area


class TestProjectionRoundtrip(unittest.TestCase):
    def test_roundtrip_error_quantified(self):
        for proj in (
            Equirectangular(),
            Equirectangular(lat0=10, lon0=120, lat_std=30),
            LambertCylindricalEqualArea(),
            LambertCylindricalEqualArea(lat_std=45, lon0=-60),
        ):
            max_err, mean_err, n = roundtrip_errors(proj)
            # 往返误差必须可量化且处于毫米级以下
            self.assertLess(max_err, 1e-3, proj.name)
            self.assertGreater(n, 1000)

    def test_roundtrip_poles_and_antimeridian(self):
        for proj in (Equirectangular(), LambertCylindricalEqualArea()):
            for lat, lon in [(90, 0), (-90, 179), (0, 180), (0, -180), (89.999, -179.999)]:
                lat2, lon2 = proj.inverse(*proj.forward(lat, lon))
                self.assertAlmostEqual(lat, lat2, places=9)
                self.assertLess(haversine(lat, lon, lat2, lon2), 1e-3)

    def test_known_values(self):
        # 赤道上 1° ≈ 111.195 km（平均半径）
        eq = Equirectangular()
        x, y = eq.forward(0, 1)
        self.assertAlmostEqual(x, R * math.pi / 180, places=6)
        self.assertAlmostEqual(y, 0.0, places=9)
        # Lambert 等积：极点 y = ±R
        lc = LambertCylindricalEqualArea()
        self.assertAlmostEqual(lc.forward(90, 33)[1], R, places=6)
        self.assertAlmostEqual(lc.forward(-90, -77)[1], -R, places=6)

    def test_equal_area_property(self):
        # 等积圆柱：同一矩形投影面积应与球面面积一致（相对误差 <1e-12）
        lc = LambertCylindricalEqualArea()
        for lat1, lat2, lon1, lon2 in [(0, 10, 0, 20), (60, 70, -30, 40), (-80, -60, 100, 170)]:
            poly = rect(lat1, lat2, lon1, lon2)
            pts = [lc.forward(*p) for p in poly]
            s = 0.0
            for i in range(4):
                x1, y1 = pts[i]
                x2, y2 = pts[(i + 1) % 4]
                s += x1 * y2 - x2 * y1
            planar = abs(s) / 2
            self.assertAlmostEqual(planar / analytic_rect_area(lat1, lat2, lon2 - lon1), 1.0, places=9)


class TestDistance(unittest.TestCase):
    def test_zero_length(self):
        self.assertEqual(haversine(30, 120, 30, 120), 0.0)
        self.assertEqual(path_length([(30, 120)]), 0.0)
        self.assertEqual(path_length([(30, 120), (30, 120), (30, 120)]), 0.0)
        self.assertEqual(path_length([]), 0.0)

    def test_known_distances(self):
        # 赤道四分之一周长
        self.assertAlmostEqual(haversine(0, 0, 0, 90), math.pi * R / 2, places=6)
        # 赤道到极点
        self.assertAlmostEqual(haversine(0, 50, 90, -130), math.pi * R / 2, places=6)
        # 对跖点
        self.assertAlmostEqual(haversine(10, 0, -10, 180), math.pi * R, places=6)

    def test_antimeridian_short_way(self):
        # 179.5°E 到 179.5°W 只有 1°，不能绕 359°
        d = haversine(0, 179.5, 0, -179.5)
        self.assertAlmostEqual(d, R * math.pi / 180, places=6)
        self.assertLess(d, 112000)
        # 高纬跨线
        d2 = haversine(70, 170, 70, -170)
        self.assertAlmostEqual(d2, haversine(70, 170, 70, 190), places=6)

    def test_pole_longitude_irrelevant(self):
        self.assertLess(haversine(90, 0, 90, 120), 1e-6)
        self.assertAlmostEqual(haversine(-90, -45, 0, 100), math.pi * R / 2, places=6)


class TestPolygonArea(unittest.TestCase):
    def test_degenerate(self):
        self.assertEqual(spherical_polygon_area([]), 0.0)
        self.assertEqual(spherical_polygon_area([(10, 20)]), 0.0)          # 单点
        self.assertEqual(spherical_polygon_area([(10, 20), (30, 40)]), 0.0)  # 线
        self.assertEqual(spherical_polygon_area([(10, 20)] * 5), 0.0)        # 重复点

    def test_equator_rect_exact(self):
        # 矢量法与独立参照（大圆弧加密）一致
        poly = rect(0, 10, 0, 20)
        a = spherical_polygon_area(poly)
        self.assertAlmostEqual(a / reference_spherical_area(poly), 1.0, places=9)

    def test_rect_matches_analytic(self):
        # 小矩形大圆边与纬线边差异极小
        for lat1, lat2, lon1, lon2 in [(0, 1, 0, 1), (30, 31, 100, 101), (60, 61, -50, -49)]:
            got = spherical_polygon_area(rect(lat1, lat2, lon1, lon2))
            want = analytic_rect_area(lat1, lat2, 1.0)
            self.assertAlmostEqual(got / want, 1.0, places=4)

    def test_antimeridian_polygon(self):
        # 跨 ±180°：170E..-170W 宽 20°，与 170E..190E 完全等价
        a = spherical_polygon_area(rect(10, 20, 170, -170))
        b = spherical_polygon_area(rect(10, 20, 170, 190))
        self.assertAlmostEqual(a / b, 1.0, places=12)
        self.assertAlmostEqual(a / analytic_rect_area(10, 20, 20), 1.0, delta=0.02)
        self.assertAlmostEqual(a / reference_spherical_area(rect(10, 20, 170, -170)),
                               1.0, places=9)
        self.assertGreater(a, 0)

    def test_polar_cap_polygon(self):
        # 绕北极的三角形：面积必须为正且小于 80°N 以北的整个极冠
        cap = 2 * math.pi * R * R * (1 - math.sin(math.radians(80)))
        tri = [(80, 0), (80, 120), (80, -120)]
        a = spherical_polygon_area(tri)
        self.assertGreater(a, 0)
        self.assertLess(a, cap)
        self.assertAlmostEqual(a / reference_spherical_area(tri), 1.0, places=9)

    def test_hemisphere_crossing(self):
        # 跨赤道（跨半球）矩形
        poly = rect(-10, 10, 0, 30)
        a = spherical_polygon_area(poly)
        want = analytic_rect_area(-10, 10, 30)
        self.assertAlmostEqual(a / want, 1.0, delta=0.05)
        self.assertAlmostEqual(a / reference_spherical_area(poly), 1.0, places=9)
        # 覆盖超过半球的输入：返回较小一侧，确定且非负
        huge = rect(-80, 80, 0, 350)
        a2 = spherical_polygon_area(huge)
        self.assertGreaterEqual(a2, 0)
        self.assertLessEqual(a2, 2 * math.pi * R * R)

    def test_winding_direction_and_sign(self):
        poly = rect(0, 10, 0, 20)
        a1 = spherical_polygon_area(poly)
        a2 = spherical_polygon_area(list(reversed(poly)))
        self.assertAlmostEqual(a1, a2, places=3)
        self.assertGreaterEqual(a1, 0)

    def test_full_earth_bounds(self):
        # 任何结果都不超过一个半球
        for poly in [rect(-90, 90, 0, 359.999), rect(0, 90, 0, 180), rect(-90, 0, -180, 0)]:
            a = spherical_polygon_area(poly)
            self.assertGreaterEqual(a, 0)
            self.assertLessEqual(a, 2 * math.pi * R * R + 1e-3)

    def test_clamp_lon(self):
        self.assertEqual(clamp_lon(190), -170)
        self.assertEqual(clamp_lon(-180), 180)
        self.assertEqual(clamp_lon(540), 180)


class TestPlanarVsSpherical(unittest.TestCase):
    def test_planar_close_at_small_scale(self):
        # 小范围（0.1°）平面近似与球面面积相对差 < 0.01%
        poly = rect(30, 30.1, 100, 100.1)
        rel = abs(planar_polygon_area(poly) - spherical_polygon_area(poly)) / spherical_polygon_area(poly)
        self.assertLess(rel, 1e-4)

    def test_planar_antimeridian(self):
        poly = rect(10, 20, 170, -170)
        a = planar_polygon_area(poly)
        self.assertGreater(a, 0)
        self.assertAlmostEqual(a / spherical_polygon_area(poly), 1.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()
