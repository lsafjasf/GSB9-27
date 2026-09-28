"""numclose 的性质断言测试与边界用例。

运行：python3 -m unittest test_numclose -v
"""

import math
import unittest

from numclose import isclose, tolerance, abs_error, rel_error

INF = math.inf
NAN = math.nan
MAX_FLOAT = 1.7976931348623157e308   # 有限双精度最大值
MIN_DENORMAL = 5e-324                # 最小正非规格化数
MIN_NORMAL = 2.2250738585072014e-308  # 最小正规格化数

# 性质断言用的值语料：覆盖极小值、极大值、跨零点、量级悬殊、特殊值。
CORPUS = [
    0.0, -0.0, 1.0, -1.0, 2.0, 0.5, -0.5,
    1e-300, -1e-300, 1e300, -1e300,
    MIN_DENORMAL, -MIN_DENORMAL, MIN_NORMAL, -MIN_NORMAL,
    MAX_FLOAT, -MAX_FLOAT,
    INF, -INF, NAN,
]

TOLS = [
    (0.0, 0.0),
    (1e-15, 0.0),
    (1e-9, 0.0),
    (1e-6, 1e-12),
    (1e-3, 1e-9),
    (0.1, 1e-6),
    (0.5, 0.01),
]


class TestAlgebraicProperties(unittest.TestCase):
    """可断言的代数性质：自反、对称、容差单调。"""

    def test_reflexivity_finite(self):
        for v in CORPUS:
            if isinstance(v, float) and math.isnan(v):
                continue
            for rel, abst in TOLS:
                with self.subTest(v=v, rel=rel, abs=abst):
                    self.assertTrue(isclose(v, v, rel_tol=rel, abs_tol=abst))

    def test_symmetry(self):
        for a in CORPUS:
            for b in CORPUS:
                for rel, abst in TOLS:
                    with self.subTest(a=a, b=b, rel=rel, abs=abst):
                        self.assertEqual(
                            isclose(a, b, rel_tol=rel, abs_tol=abst),
                            isclose(b, a, rel_tol=rel, abs_tol=abst),
                        )

    def test_tolerance_monotonicity(self):
        # 容差放宽（两个容差都不减小）不应让相等变不相等。
        ordered = sorted(TOLS)  # 按 (rel, abs) 字典序构造链
        chain = [(0.0, 0.0), (1e-15, 1e-300), (1e-9, 1e-12),
                 (1e-6, 1e-9), (1e-3, 1e-6), (0.1, 1e-3), (0.5, 0.01)]
        for a in CORPUS:
            for b in CORPUS:
                results = [isclose(a, b, rel_tol=r, abs_tol=t)
                           for r, t in chain]
                for lo, hi in zip(results, results[1:]):
                    with self.subTest(a=a, b=b):
                        self.assertFalse(lo and not hi,
                                         "放宽容差后相等变成了不相等")

    def test_agrees_with_math_isclose_on_finite(self):
        # 交叉验证：有限值上与标准库 math.isclose 结论一致。
        for a in CORPUS:
            for b in CORPUS:
                if not (math.isfinite(a) and math.isfinite(b)):
                    continue
                for rel, abst in TOLS:
                    with self.subTest(a=a, b=b, rel=rel, abs=abst):
                        self.assertEqual(
                            isclose(a, b, rel_tol=rel, abs_tol=abst),
                            math.isclose(a, b, rel_tol=rel, abs_tol=abst),
                        )


class TestSpecialValues(unittest.TestCase):
    """NaN、无穷、正负零的明确行为。"""

    def test_nan_never_close(self):
        self.assertFalse(isclose(NAN, NAN))
        self.assertFalse(isclose(NAN, 1.0))
        self.assertFalse(isclose(1.0, NAN))
        self.assertFalse(isclose(NAN, INF))
        self.assertFalse(isclose(NAN, NAN, rel_tol=1.0, abs_tol=1e300))

    def test_infinity(self):
        self.assertTrue(isclose(INF, INF))
        self.assertTrue(isclose(-INF, -INF))
        self.assertFalse(isclose(INF, -INF))
        self.assertFalse(isclose(INF, MAX_FLOAT))
        self.assertFalse(isclose(INF, 1.0, rel_tol=1.0, abs_tol=1e300))
        self.assertFalse(isclose(-INF, -MAX_FLOAT))

    def test_signed_zero(self):
        self.assertTrue(isclose(0.0, -0.0))
        self.assertTrue(isclose(-0.0, 0.0, rel_tol=0.0, abs_tol=0.0))
        self.assertEqual(rel_error(0.0, -0.0), 0.0)

    def test_invalid_tolerances_rejected(self):
        for bad in (-1e-9, -0.1, NAN, INF, -INF):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    isclose(1.0, 1.0, rel_tol=bad)
                with self.assertRaises(ValueError):
                    isclose(1.0, 1.0, abs_tol=bad)
        with self.assertRaises(TypeError):
            isclose(1.0, 1.0, rel_tol="0.1")
        with self.assertRaises(TypeError):
            isclose(1.0, 1.0, abs_tol=True)


class TestBoundaryCases(unittest.TestCase):
    """极小值、极大值、跨零点、量级差异十个数量级。"""

    def test_denormal_min_vs_zero(self):
        # 极小值：纯相对容差在零附近退化，5e-324 与 0 不接近；
        # 只有 abs_tol 覆盖时才接近。
        self.assertFalse(isclose(MIN_DENORMAL, 0.0))
        self.assertFalse(isclose(MIN_DENORMAL, 0.0, rel_tol=0.5))
        self.assertTrue(isclose(MIN_DENORMAL, 0.0, abs_tol=MIN_DENORMAL))
        self.assertTrue(isclose(-MIN_DENORMAL, MIN_DENORMAL,
                                abs_tol=2 * MIN_DENORMAL))

    def test_max_float(self):
        # 极大值：相邻浮点数相对间隔约 1.1e-16。
        neighbor = math.nextafter(MAX_FLOAT, 0.0)
        self.assertFalse(isclose(MAX_FLOAT, neighbor, rel_tol=1e-16))
        self.assertTrue(isclose(MAX_FLOAT, neighbor, rel_tol=1e-15))
        # 大数异号相减溢出为 inf，判定必须稳定为 False 而非抛异常。
        self.assertFalse(isclose(MAX_FLOAT, -MAX_FLOAT,
                                 rel_tol=0.5, abs_tol=MAX_FLOAT))

    def test_crossing_zero(self):
        # 跨零点：相对容差无法弥合符号差异，绝对容差可以。
        self.assertFalse(isclose(-1e-12, 1e-12, rel_tol=0.1))
        self.assertTrue(isclose(-1e-12, 1e-12, rel_tol=0.1, abs_tol=1e-11))
        self.assertFalse(isclose(-1e-12, 1e-12, abs_tol=1e-12))

    def test_ten_orders_of_magnitude(self):
        # 量级差十个数量级：默认容差下不接近。
        self.assertFalse(isclose(1.0, 1e-10))
        self.assertFalse(isclose(1.0, 1e10))
        # 同一量级内的相对扰动：接近。
        self.assertTrue(isclose(1e10, 1e10 * (1 + 1e-10)))
        self.assertTrue(isclose(1e-10, 1e-10 * (1 + 1e-10)))
        # 大数加小量被吞掉：1 + 1e-17 在 1e16 量级上不可分辨。
        self.assertTrue(isclose(1e16, 1e16 + 1.0))
        self.assertFalse(isclose(1e16, 1e16 + 1e8))

    def test_near_zero_degenerates_to_abs_tol(self):
        # 接近零时 rel_tol 失效：rel 项趋于 0，只剩 abs_tol 起作用。
        self.assertFalse(isclose(1e-8, 0.0, rel_tol=0.9))
        self.assertTrue(isclose(1e-8, 0.0, rel_tol=0.9, abs_tol=1e-7))
        self.assertEqual(tolerance(1e-8, 0.0, rel_tol=0.9, abs_tol=1e-7),
                         1e-7)


class TestHelpers(unittest.TestCase):

    def test_tolerance_value(self):
        self.assertEqual(tolerance(100.0, 50.0, rel_tol=1e-3, abs_tol=1.0),
                         max(1e-3 * 100.0, 1.0))
        self.assertEqual(tolerance(1e-9, 2e-9, rel_tol=1e-3, abs_tol=1e-6),
                         1e-6)
        with self.assertRaises(ValueError):
            tolerance(INF, 1.0)
        with self.assertRaises(ValueError):
            tolerance(NAN, 1.0)

    def test_abs_error(self):
        self.assertEqual(abs_error(3.0, 1.0), 2.0)
        self.assertEqual(abs_error(1.0, 3.0), 2.0)
        self.assertEqual(abs_error(MAX_FLOAT, -MAX_FLOAT), INF)

    def test_rel_error(self):
        self.assertAlmostEqual(rel_error(1.0, 1.1), 0.1 / 1.1)
        self.assertEqual(rel_error(1.0, 1.1), rel_error(1.1, 1.0))  # 对称
        self.assertEqual(rel_error(0.0, 0.0), 0.0)
        self.assertEqual(rel_error(0.0, 1.0), 1.0)
        self.assertEqual(rel_error(INF, 1.0), INF)
        self.assertTrue(math.isnan(rel_error(NAN, 1.0)))


if __name__ == "__main__":
    unittest.main()
