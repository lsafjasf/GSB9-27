"""heightmap 库自测：平缓地形、陡坡、平台、空洞、边界与法线连续性。

运行：python3 -m unittest test_heightmap -v   或   python3 test_heightmap.py
"""

import math
import random
import unittest

from heightmap import Heightmap, QueryResult, Status

METHODS = (Heightmap.BILINEAR, Heightmap.BICUBIC)


def make_grid(nx, ny, fn):
    return [[fn(i, j) for i in range(nx)] for j in range(ny)]


class TestFlatTerrain(unittest.TestCase):
    """平缓地形：常数高度场，任意插值都必须精确。"""

    def setUp(self):
        self.hm = Heightmap(make_grid(8, 8, lambda i, j: 12.5), cell_size=2.0)

    def test_height_exact(self):
        rng = random.Random(1)
        for _ in range(200):
            x = rng.uniform(0, 14)
            y = rng.uniform(0, 14)
            for m in METHODS:
                self.assertAlmostEqual(self.hm.height(x, y, m), 12.5, places=12)

    def test_normal_vertical_slope_zero(self):
        for m in METHODS:
            r = self.hm.query(5.3, 7.7, m)
            self.assertEqual(r.status, Status.OK)
            self.assertAlmostEqual(r.normal[0], 0.0, places=12)
            self.assertAlmostEqual(r.normal[1], 0.0, places=12)
            self.assertAlmostEqual(r.normal[2], 1.0, places=12)
            self.assertAlmostEqual(r.slope_deg, 0.0, places=9)


class TestSteepRamp(unittest.TestCase):
    """陡坡：z = 10x + 3y + 5 的平面（约 84.4 度坡度方向梯度）。

    双线性与 Catmull-Rom 都能精确复现线性曲面，法线应全场恒定。
    """

    def setUp(self):
        self.hm = Heightmap(
            make_grid(10, 10, lambda i, j: 10.0 * i + 3.0 * j + 5.0), cell_size=1.0
        )

    def test_plane_exact(self):
        rng = random.Random(2)
        for _ in range(300):
            x = rng.uniform(0, 9)
            y = rng.uniform(0, 9)
            for m in METHODS:
                self.assertAlmostEqual(
                    self.hm.height(x, y, m), 10.0 * x + 3.0 * y + 5.0, places=9
                )

    def test_normal_and_slope(self):
        expected_len = math.sqrt(10.0 * 10.0 + 3.0 * 3.0 + 1.0)
        expected = (-10.0 / expected_len, -3.0 / expected_len, 1.0 / expected_len)
        expected_slope = math.degrees(math.atan(math.hypot(10.0, 3.0)))
        rng = random.Random(3)
        for _ in range(100):
            x = rng.uniform(0, 9)
            y = rng.uniform(0, 9)
            for m in METHODS:
                r = self.hm.query(x, y, m)
                for a, b in zip(r.normal, expected):
                    self.assertAlmostEqual(a, b, places=9)
                self.assertAlmostEqual(r.slope_deg, expected_slope, places=9)
        self.assertGreater(expected_slope, 84.0)  # 确属陡坡


class TestPlatform(unittest.TestCase):
    """平台：中央 4<=i<=5, 4<=j<=5 区域高 20，其余为 0。"""

    def setUp(self):
        self.hm = Heightmap(
            make_grid(11, 11, lambda i, j: 20.0 if 4 <= i <= 6 and 4 <= j <= 6 else 0.0)
        )

    def test_plateau_top_and_flat_ground(self):
        for m in METHODS:
            # 平台内部（距边缘 >=1 个单元，双三次窗口不触及台阶）
            r = self.hm.query(5.0, 5.0, m)
            self.assertAlmostEqual(r.height, 20.0, places=9)
            self.assertAlmostEqual(r.slope_deg, 0.0, places=9)
            # 远离平台的平地
            r = self.hm.query(1.5, 1.5, m)
            self.assertAlmostEqual(r.height, 0.0, places=9)
            self.assertAlmostEqual(r.normal[2], 1.0, places=9)

    def test_step_transition_bounded(self):
        # 跨过台阶边缘 x=4 的剖面：高度单调过渡且被 [0, 20] 包住（双线性）
        prev = -1.0
        for k in range(41):
            x = 3.0 + k * (2.0 / 40.0)
            h = self.hm.height(x, 5.0, Heightmap.BILINEAR)
            self.assertGreaterEqual(h, -1e-12)
            self.assertLessEqual(h, 20.0 + 1e-12)
            self.assertGreaterEqual(h, prev - 1e-12)
            prev = h


class TestHoles(unittest.TestCase):
    """空洞：None 与 NaN 都必须识别，查询返回 NODATA 而不是 0。"""

    def setUp(self):
        # 10x10，中央 4<=i<=5, 4<=j<=5 为 None，(8,8) 为 NaN
        def fn(i, j):
            if 4 <= i <= 5 and 4 <= j <= 5:
                return None
            return 50.0

        grid = make_grid(10, 10, fn)
        grid[8][8] = float("nan")
        self.hm = Heightmap(grid)

    def test_hole_center_is_nodata(self):
        for m in METHODS:
            r = self.hm.query(4.5, 4.5, m)
            self.assertEqual(r.status, Status.NODATA)
            self.assertIsNone(r.height)
            self.assertIsNone(r.normal)
            self.assertIsNone(r.slope_deg)

    def test_hole_not_zero(self):
        # 历史 bug 形态：空洞被当成 0 高度。这里显式断言不会返回 0。
        r = self.hm.query(4.5, 4.5)
        self.assertNotEqual(r.height, 0.0)
        self.assertIsNone(r.height)

    def test_cell_touching_hole_bilinear(self):
        # 双线性：单元 (3,3)-(4,4) 有一个角点 (4,4) 是空洞 -> NODATA
        r = self.hm.query(3.5, 3.5, Heightmap.BILINEAR)
        self.assertEqual(r.status, Status.NODATA)

    def test_bicubic_needs_wider_neighborhood(self):
        # 双三次：4x4 窗口，距空洞 1 个单元的点也受影响
        r = self.hm.query(3.0, 3.0, Heightmap.BICUBIC)
        self.assertEqual(r.status, Status.NODATA)

    def test_far_from_hole_ok(self):
        for m in METHODS:
            r = self.hm.query(1.0, 1.0, m)
            self.assertEqual(r.status, Status.OK)
            self.assertAlmostEqual(r.height, 50.0, places=9)

    def test_nan_hole(self):
        r = self.hm.query(8.0, 8.0, Heightmap.BILINEAR)
        self.assertEqual(r.status, Status.NODATA)


class TestBoundaries(unittest.TestCase):
    """边界用例：角点、边缘、网格外。"""

    def setUp(self):
        self.hm = Heightmap(
            make_grid(5, 5, lambda i, j: float(i * 10 + j)), cell_size=2.0,
            origin=(100.0, 200.0),
        )

    def test_node_exactness(self):
        # 查询恰好落在节点上时必须返回原始采样值
        for j in range(5):
            for i in range(5):
                x = 100.0 + i * 2.0
                y = 200.0 + j * 2.0
                for m in METHODS:
                    self.assertAlmostEqual(
                        self.hm.height(x, y, m), float(i * 10 + j), places=9
                    )

    def test_corners_and_edges(self):
        xmin, ymin, xmax, ymax = self.hm.bounds
        for m in METHODS:
            self.assertEqual(self.hm.query(xmin, ymin, m).status, Status.OK)
            self.assertEqual(self.hm.query(xmax, ymax, m).status, Status.OK)
            self.assertEqual(self.hm.query(xmax, (ymin + ymax) / 2, m).status, Status.OK)
            self.assertEqual(self.hm.query((xmin + xmax) / 2, ymax, m).status, Status.OK)

    def test_out_of_bounds(self):
        xmin, ymin, xmax, ymax = self.hm.bounds
        eps = 1e-9
        for x, y in [
            (xmin - eps, ymin),
            (xmax + eps, ymin),
            (xmin, ymin - eps),
            (xmin, ymax + eps),
            (xmin - 100, ymax + 100),
        ]:
            r = self.hm.query(x, y)
            self.assertEqual(r.status, Status.OUT_OF_BOUNDS)
            self.assertIsNone(r.height)

    def test_minimal_2x2_grid(self):
        hm = Heightmap([[0.0, 1.0], [2.0, 3.0]])
        for m in METHODS:
            self.assertAlmostEqual(hm.height(0.5, 0.5, m), 1.5, places=9)

    def test_invalid_construction(self):
        with self.assertRaises(ValueError):
            Heightmap([[1.0]])                      # 太小
        with self.assertRaises(ValueError):
            Heightmap([[1.0, 2.0], [1.0]])          # 非矩形
        with self.assertRaises(ValueError):
            Heightmap([[1.0, 2.0], [3.0, 4.0]], cell_size=0)  # 非法分辨率
        with self.assertRaises(ValueError):
            Heightmap([[1.0, 2.0], [3.0, 4.0]]).query(0.5, 0.5, "cubic-x")  # 未知方法


class TestNormalContinuity(unittest.TestCase):
    """法线连续性：跨单元边采样法线夹角。

    双三次（C1）法线必须连续；双线性（C0）允许跳变，但跳变量应有限。
    """

    def setUp(self):
        # 波动地形，保证梯度沿单元边确实变化
        self.hm = Heightmap(
            make_grid(
                33, 33,
                lambda i, j: 5.0 * math.sin(i / 4.0) * math.cos(j / 5.0) + 0.1 * i,
            )
        )

    @staticmethod
    def _max_edge_jump(hm, method):
        eps = 1e-7
        worst = 0.0
        # 竖直单元边 x = k 与水平单元边 y = k
        for k in range(1, 32):
            for t in (0.25, 0.5, 0.75):
                y = (k - 1 + t)
                x = (k - 1 + t)
                for a, b in [
                    ((k - eps, y), (k + eps, y)),
                    ((x, k - eps), (x, k + eps)),
                ]:
                    na = hm.normal(*a, method)
                    nb = hm.normal(*b, method)
                    if na and nb:
                        dot = max(-1.0, min(1.0, sum(p * q for p, q in zip(na, nb))))
                        worst = max(worst, math.degrees(math.acos(dot)))
        return worst

    def test_bicubic_normals_continuous(self):
        jump = self._max_edge_jump(self.hm, Heightmap.BICUBIC)
        self.assertLess(jump, 1e-4)  # C1：跳变量级仅浮点误差

    def test_bilinear_jump_larger_than_bicubic(self):
        j_lin = self._max_edge_jump(self.hm, Heightmap.BILINEAR)
        j_cub = self._max_edge_jump(self.hm, Heightmap.BICUBIC)
        self.assertGreater(j_lin, max(1e-6, 1000.0 * j_cub))


class TestAccuracyAgainstAnalyticSurface(unittest.TestCase):
    """与真实解析曲面对比的精度回归（宽松阈值，精确数据见 evaluate.py 输出）。"""

    def setUp(self):
        # 平缓起伏：幅 10，波长 ~250/200，最大坡度约 14 度
        self.f = lambda x, y: 10.0 * math.sin(2 * math.pi * x / 250.0) * math.cos(
            2 * math.pi * y / 200.0
        )
        n, cell = 129, 2.0
        grid = [[self.f(i * cell, j * cell) for i in range(n)] for j in range(n)]
        self.hm = Heightmap(grid, cell_size=cell)

    def _rms_max(self, method, samples=4000):
        rng = random.Random(7)
        errs = []
        for _ in range(samples):
            x = rng.uniform(0, 256)
            y = rng.uniform(0, 256)
            errs.append(abs(self.hm.height(x, y, method) - self.f(x, y)))
        rms = math.sqrt(sum(e * e for e in errs) / len(errs))
        return rms, max(errs)

    def test_error_thresholds(self):
        rms_lin, max_lin = self._rms_max(Heightmap.BILINEAR)
        rms_cub, max_cub = self._rms_max(Heightmap.BICUBIC)
        self.assertLess(rms_lin, 0.02)
        self.assertLess(rms_cub, 0.002)
        self.assertLess(rms_cub, rms_lin)  # 曲面插值应明显更准


if __name__ == "__main__":
    unittest.main(verbosity=2)
