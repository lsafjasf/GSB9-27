#!/usr/bin/env python3
"""自测：边界用例 + 偏差上界 + 闭合折线 + 自交检测。仅标准库 unittest。"""
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from polyline_simplify import (
    simplify, verify_analytic, verify_sampling,
    find_self_intersections, point_segment_distance,
)


class TestEdgeCases(unittest.TestCase):
    def test_two_points(self):
        pts = [(0.0, 0.0), (10.0, 0.0)]
        res = simplify(pts, 1.0)
        self.assertEqual(res.indices, [0, 1])

    def test_single_point(self):
        res = simplify([(1.0, 2.0)], 1.0)
        self.assertEqual(res.indices, [0])

    def test_all_collinear(self):
        pts = [(float(i), 2.0 * i) for i in range(1000)]
        res = simplify(pts, 1e-9)
        self.assertEqual(res.indices, [0, 999])  # 全共线只留端点
        ok, dev, _ = verify_analytic(pts, res.indices, 1e-9)
        self.assertTrue(ok)

    def test_huge_tolerance(self):
        rng = random.Random(1)
        pts = [(rng.random() * 100, rng.random() * 100) for _ in range(500)]
        res = simplify(pts, 1e9)
        self.assertEqual(res.indices, [0, 499])  # 阈值极大只留端点

    def test_tiny_tolerance_keeps_all(self):
        rng = random.Random(2)
        pts = [(rng.random() * 100, rng.random() * 100) for _ in range(300)]
        res = simplify(pts, 0.0)
        self.assertEqual(res.indices, list(range(300)))  # 阈值极小全保留

    def test_duplicate_points(self):
        pts = [(0.0, 0.0)] * 5 + [(1.0, 1.0)] * 5
        res = simplify(pts, 0.5)
        self.assertEqual(res.indices, [0, 9])
        ok, _, _ = verify_analytic(pts, res.indices, 0.5)
        self.assertTrue(ok)

    def test_negative_tolerance_rejected(self):
        with self.assertRaises(ValueError):
            simplify([(0, 0), (1, 1)], -1.0)


class TestDeviationBound(unittest.TestCase):
    def _check(self, pts, tol, closed=False):
        res = simplify(pts, tol)
        ok_a, dev_a, _ = verify_analytic(pts, res.indices, tol, res.closed, leaf=res.leaf)
        ok_s, dev_s, _ = verify_sampling(pts, res.indices, tol, res.closed, leaf=res.leaf)
        self.assertTrue(ok_a, f"analytic dev {dev_a} > tol {tol}")
        self.assertTrue(ok_s, f"sampling dev {dev_s} > tol {tol}")
        self.assertLessEqual(dev_a, tol + 1e-9)
        return res

    def test_random_walk_bounds(self):
        rng = random.Random(3)
        x = y = 0.0
        pts = []
        for _ in range(5000):
            x += rng.gauss(0, 1)
            y += rng.gauss(0, 1)
            pts.append((x, y))
        for tol in (0.01, 0.5, 2.0, 10.0):
            self._check(pts, tol)

    def test_circle_bounds(self):
        pts = [(math.cos(t), math.sin(t))
               for t in [2 * math.pi * i / 2000 for i in range(2000)]]
        for tol in (1e-4, 1e-2, 0.5):
            self._check(pts, tol)

    def test_decision_deviations_consistent(self):
        # 每次决策记录的偏差值：保留决策的偏差 > tol，舍弃决策的偏差 <= tol
        rng = random.Random(4)
        pts = [(rng.random() * 50, rng.random() * 50) for _ in range(500)]
        tol = 1.0
        res = simplify(pts, tol)
        self.assertTrue(res.decisions)
        for d in res.decisions:
            if d.kept:
                self.assertGreater(d.deviation, tol)
                self.assertIsNotNone(d.split)
            else:
                self.assertLessEqual(d.deviation, tol + 1e-12)


class TestClosedPolyline(unittest.TestCase):
    def test_start_point_preserved(self):
        pts = [(math.cos(t), math.sin(t))
               for t in [2 * math.pi * i / 500 for i in range(500)]]
        pts.append(pts[0])  # 闭合
        res = simplify(pts, 0.01)
        self.assertTrue(res.closed)
        self.assertEqual(res.indices[0], 0)  # 起点必保留

    def test_closed_deviation_bound(self):
        pts = [(math.cos(t) * (1 + 0.1 * math.sin(5 * t)),
                math.sin(t) * (1 + 0.1 * math.sin(5 * t)))
               for t in [2 * math.pi * i / 1000 for i in range(1000)]]
        pts.append(pts[0])
        for tol in (0.001, 0.05, 0.5):
            res = simplify(pts, tol)
            ok_a, dev_a, _ = verify_analytic(pts, res.indices, tol, True, leaf=res.leaf)
            ok_s, dev_s, _ = verify_sampling(pts, res.indices, tol, True, leaf=res.leaf)
            self.assertTrue(ok_a, f"closed analytic dev {dev_a} > {tol}")
            self.assertTrue(ok_s, f"closed sampling dev {dev_s} > {tol}")

    def test_closed_square(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        res = simplify(pts, 0.1)
        self.assertEqual(res.indices, [0, 1, 2, 3])  # 方角全保留
        self.assertEqual(res.self_intersections, [])


class TestSelfIntersection(unittest.TestCase):
    def test_detects_figure_eight(self):
        # 8 字形：线段 0 与线段 2 相交
        pts = [(0, 0), (2, 2), (2, 0), (0, 2)]
        hits = find_self_intersections(pts, closed=False)
        self.assertIn((0, 2), hits)

    def test_no_false_positive_on_simple_polyline(self):
        pts = [(0, 0), (1, 1), (2, 0), (3, 1)]
        self.assertEqual(find_self_intersections(pts), [])

    def test_closed_self_intersection_reported(self):
        # 五角星式闭合折线必然自交，simplify 应报告
        star = [(math.cos(4 * math.pi * i / 5), math.sin(4 * math.pi * i / 5))
                for i in range(5)]
        star.append(star[0])
        res = simplify(star, 0.0)  # tol=0 全保留，保留自交
        self.assertTrue(res.self_intersections)

    def test_simplified_circle_no_self_intersection(self):
        pts = [(math.cos(t), math.sin(t))
               for t in [2 * math.pi * i / 2000 for i in range(2000)]]
        pts.append(pts[0])
        res = simplify(pts, 0.05)
        self.assertEqual(res.self_intersections, [])


class TestGeometry(unittest.TestCase):
    def test_point_segment_distance(self):
        self.assertAlmostEqual(
            point_segment_distance((0, 1), (0, 0), (2, 0)), 1.0)
        self.assertAlmostEqual(
            point_segment_distance((5, 0), (0, 0), (2, 0)), 3.0)  # 落在线段外
        self.assertAlmostEqual(
            point_segment_distance((1, 1), (1, 1), (1, 1)), 0.0)  # 退化线段


if __name__ == "__main__":
    unittest.main(verbosity=2)
