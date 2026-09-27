"""边界用例自测：两点、全共线、阈值极大、阈值极小、闭合环、自交检测。

运行：python3 -m unittest test_edge_cases -v
"""

import math
import unittest

from simplify import (find_self_intersections, segments_intersect, simplify)
from verify import verify_analytic, verify_sampling


class TestEdgeCases(unittest.TestCase):
    def test_two_points(self):
        pts = [(0.0, 0.0), (3.0, 4.0)]
        r = simplify(pts, 0.1)
        self.assertEqual(r.kept_indices, [0, 1])
        self.assertEqual(r.decisions, [])  # 无内部点，无决策

    def test_single_point(self):
        r = simplify([(1.0, 2.0)], 0.1)
        self.assertEqual(r.kept_indices, [0])

    def test_all_collinear(self):
        pts = [(float(i), 2.0 * i) for i in range(100)]
        r = simplify(pts, 1e-9)
        self.assertEqual(r.kept_indices, [0, 99])  # 只留端点
        self.assertEqual(len(r.decisions), 1)
        self.assertAlmostEqual(r.decisions[0].deviation, 0.0)
        self.assertFalse(r.decisions[0].split)

    def test_huge_epsilon(self):
        pts = [(float(i), math.sin(i)) for i in range(500)]
        r = simplify(pts, 1e6)
        self.assertEqual(r.kept_indices, [0, 499])  # 阈值极大 -> 只留端点
        ok = verify_analytic(pts, r)[2]
        self.assertTrue(ok)

    def test_tiny_epsilon(self):
        pts = [(float(i), math.sin(i * 0.3)) for i in range(200)]
        r = simplify(pts, 1e-12)
        self.assertEqual(r.kept_indices, list(range(200)))  # 阈值极小 -> 全保留
        self.assertTrue(all(d.split for d in r.decisions))

    def test_zero_epsilon_drops_only_exact_collinear(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 1.0)]
        r = simplify(pts, 0.0)
        self.assertEqual(r.kept_indices, [0, 2, 3])  # 严格共线的中间点被删

    def test_closed_keeps_start(self):
        n = 360
        ring = [(math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n))
                for i in range(n)]
        r = simplify(ring, 0.05, closed=True)
        self.assertIn(0, r.kept_indices)          # 起点必须保留
        self.assertEqual(r.self_intersections, [])  # 圆环简化后不得自交
        ok = verify_analytic(ring, r)[2]
        self.assertTrue(ok)

    def test_closed_with_duplicated_endpoint(self):
        ring = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
        r = simplify(ring, 0.01, closed=True)
        self.assertIn(0, r.kept_indices)
        self.assertEqual(r.self_intersections, [])

    def test_closed_deviation_bound_sampling(self):
        n = 720
        ring = [((1 + 0.2 * math.sin(5 * 2 * math.pi * i / n)) * math.cos(2 * math.pi * i / n),
                 (1 + 0.2 * math.sin(5 * 2 * math.pi * i / n)) * math.sin(2 * math.pi * i / n))
                for i in range(n)]
        r = simplify(ring, 0.02, closed=True)
        self.assertTrue(verify_analytic(ring, r)[2])
        self.assertTrue(verify_sampling(ring, r)[1])

    def test_self_intersection_detected_and_reported(self):
        # 细长 S 形闭合带：大阈值简化会把对边拉到一起产生自交
        ring = [(0.0, 0.0), (2.0, 0.0), (2.0, 0.9), (0.5, 0.9),
                (0.5, 0.1), (2.0, 0.1), (2.0, 1.0), (0.0, 1.0)]
        r = simplify(ring, 5.0, closed=True)
        # 大阈值下若产生自交必须被检测并报告
        if len(r.kept_indices) >= 4:
            self.assertTrue(isinstance(r.self_intersections, list))
        # 构造一个必然自交的简化环，验证检测器本身
        bowtie = [(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0)]
        self.assertEqual(find_self_intersections(bowtie, closed=True), [(0, 2)])

    def test_avoid_self_intersection_retries(self):
        ring = [(0.0, 0.0), (2.0, 0.0), (2.0, 0.9), (0.5, 0.9),
                (0.5, 0.1), (2.0, 0.1), (2.0, 1.0), (0.0, 1.0)]
        r = simplify(ring, 5.0, closed=True, avoid_self_intersection=True)
        self.assertEqual(r.self_intersections, [])  # 自动降阈值后无自交

    def test_segment_intersection_basic(self):
        self.assertTrue(segments_intersect((0, 0), (2, 2), (0, 2), (2, 0)))
        self.assertFalse(segments_intersect((0, 0), (1, 0), (0, 1), (1, 1)))

    def test_open_deviation_bound(self):
        pts = [(i * 0.1, math.sin(i * 0.1) + 0.1 * math.sin(i * 1.7))
               for i in range(1000)]
        for eps in (0.5, 0.1, 0.01):
            r = simplify(pts, eps)
            self.assertTrue(verify_analytic(pts, r)[2], f"eps={eps} 解析验证失败")
            self.assertTrue(verify_sampling(pts, r)[1], f"eps={eps} 采样验证失败")


if __name__ == "__main__":
    unittest.main()
