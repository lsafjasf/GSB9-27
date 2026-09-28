"""angle_utils 的幂等、边界与精度测试。运行: python3 -m unittest -v"""

import math
import random
import unittest
from decimal import Decimal, getcontext

from angle_utils import (
    FLOAT64_RELIABLE_LIMIT,
    deg2rad,
    normalize,
    normalize_180,
    normalize_360,
    normalization_error_bound,
    rad2deg,
    rotate2d,
)

getcontext().prec = 60

# π 到 70 位有效数字，用于 Decimal 高精度参考值
PI_DEC = Decimal(
    "3.141592653589793238462643383279502884197169399375105820974944592307816"
)


def dec_sin_cos(deg: Decimal):
    """用 Decimal 泰勒级数计算 sin/cos（先精确模 360 再转弧度）。"""
    deg = deg % Decimal(360)
    x = deg * PI_DEC / Decimal(180)
    sin_x = Decimal(0)
    cos_x = Decimal(0)
    term_s = x
    term_c = Decimal(1)
    n = 1
    while True:
        sin_x += term_s
        cos_x += term_c
        term_s = -term_s * x * x / ((2 * n) * (2 * n + 1))
        term_c = -term_c * x * x / ((2 * n - 1) * (2 * n))
        n += 1
        if abs(term_s) < Decimal("1e-55") and abs(term_c) < Decimal("1e-55"):
            break
    return +sin_x, +cos_x


class TestConventions(unittest.TestCase):
    """零度、负零、负角、区间边界的约定。"""

    def test_zero(self):
        self.assertEqual(normalize(0.0), 0.0)
        self.assertEqual(math.copysign(1.0, normalize(0.0)), 1.0)  # +0.0

    def test_negative_zero_canonicalized(self):
        r = normalize(-0.0)
        self.assertEqual(r, 0.0)
        self.assertEqual(math.copysign(1.0, r), 1.0)  # 必须是 +0.0
        self.assertEqual(normalize(-0.0), normalize(0.0))

    def test_negative_angles(self):
        self.assertEqual(normalize(-90.0), 270.0)          # 负角=顺时针
        self.assertEqual(normalize(-360.0), 0.0)
        self.assertEqual(normalize_180(-90.0), -90.0)      # [-180,180) 内不变
        self.assertEqual(normalize_180(-270.0), 90.0)

    def test_upper_boundary_wraps_to_start(self):
        self.assertEqual(normalize(360.0), 0.0)            # 上边界回卷
        self.assertEqual(normalize(720.0), 0.0)
        self.assertEqual(normalize_180(180.0), -180.0)     # +180 -> -180
        self.assertEqual(normalize(540.0, start=-180.0), -180.0)

    def test_lower_boundary_stays(self):
        self.assertEqual(normalize(0.0), 0.0)
        self.assertEqual(normalize_180(-180.0), -180.0)    # 左闭

    def test_nonfinite_rejected(self):
        for bad in (math.inf, -math.inf, math.nan):
            with self.assertRaises(ValueError):
                normalize(bad)

    def test_rotation_zero_is_exact_identity(self):
        x, y = rotate2d(3.0, -4.0, 0.0)
        self.assertEqual((x, y), (3.0, -4.0))

    def test_rotation_sign_convention(self):
        # 逆时针 90°：(1,0)->(0,1)；负角（顺时针）90°：(1,0)->(0,-1)
        x, y = rotate2d(1.0, 0.0, 90.0)
        self.assertAlmostEqual(x, 0.0, places=15)
        self.assertAlmostEqual(y, 1.0, places=15)
        x, y = rotate2d(1.0, 0.0, -90.0)
        self.assertAlmostEqual(x, 0.0, places=15)
        self.assertAlmostEqual(y, -1.0, places=15)


class TestIdempotency(unittest.TestCase):
    """幂等性：对已归一化的值再次归一化结果不变。"""

    def check_idempotent(self, values, start=0.0, period=360.0):
        for v in values:
            once = normalize(v, start, period)
            twice = normalize(once, start, period)
            self.assertEqual(once, twice, f"v={v!r} start={start!r}")
            self.assertGreaterEqual(once, start)
            self.assertLess(once, start + period)

    def test_idempotent_fixed_cases(self):
        values = [0.0, -0.0, 360.0, -360.0, 180.0, -180.0, 720.0,
                  1e6, -1e6, 1e16, -1e16, 359.99999999999994,
                  1.5e-13, -1.5e-13]
        self.check_idempotent(values)
        self.check_idempotent(values, start=-180.0)

    def test_idempotent_random_sweep(self):
        rng = random.Random(20260928)
        for start in (0.0, -180.0, 0.1, -1e-9):
            values = [rng.uniform(-1e9, 1e9) for _ in range(20000)]
            values += [rng.uniform(-1, 1) for _ in range(20000)]
            self.check_idempotent(values, start=start)

    def test_idempotent_near_boundaries(self):
        # 紧贴区间边界的 ±若干 ulp
        for start in (0.0, -180.0):
            values = []
            for k in range(-50, 51):
                base = start + k * 360.0
                for d in (-3, -2, -1, 0, 1, 2, 3):
                    values.append(base + d * math.ulp(360.0))
            self.check_idempotent(values, start=start)


class TestBoundaryAndHuge(unittest.TestCase):
    """零、负零、边界、上百万度。"""

    def test_million_degrees(self):
        # 10^6 = 2777*360 + 280，double 可精确表示，结果必须精确等于 280
        self.assertEqual(normalize(1_000_000.0), 280.0)
        self.assertEqual(normalize(-1_000_000.0), 80.0)
        self.assertEqual(normalize(9_999_999.0), 279.0)

    def test_powers_of_ten_up_to_1e22(self):
        # 10^n (n>=3) ≡ 280 (mod 360)；10^n 在 n<=22 时可被 double 精确表示，
        # 因此归一化结果必须分毫不差。
        for n in range(3, 23):
            angle = float(10**n)
            self.assertEqual(normalize(angle), 280.0, f"n={n}")
            self.assertEqual(normalize(-angle), 80.0, f"n={n}")

    def test_turns_plus_offset(self):
        # 整圈 + 偏移：1e16 的 ulp 为 2，+30 仍可精确表示
        self.assertEqual(normalize(1e16 + 30.0), 310.0)
        # 但 +31 会被舍入到 30 或 32：double 已无法表达该偏移
        self.assertIn(normalize(1e16 + 31.0), (30.0 + 280.0, 32.0 + 280.0))

    def test_rotation_full_turns(self):
        # 旋转整圈应回到原处（上百万度）
        x, y = rotate2d(1.0, 2.0, 360.0 * 2778)  # 1_000_080 度
        self.assertAlmostEqual(x, 1.0, places=12)
        self.assertAlmostEqual(y, 2.0, places=12)


class TestPrecision(unittest.TestCase):
    """与 Decimal 高精度解析结果比较的误差评估。"""

    def test_rotation_error_vs_decimal(self):
        # 旋转 (1,0) 转 30° + k 整圈，与 60 位 Decimal 参考值比较
        ref_sin, ref_cos = dec_sin_cos(Decimal(30))
        print("\n旋转 (1,0) by 30°+360°·10^k 的误差（vs 60位Decimal参考）:")
        print(f"{'k':>3} {'角度(度)':>16} {'ulp(角度)':>12} {'|Δcos|':>12} {'|Δsin|':>12}")
        for k in range(0, 13):
            angle = 30.0 + 360.0 * 10**k
            x, y = rotate2d(1.0, 0.0, angle)
            err_c = abs(Decimal(x) - ref_cos)
            err_s = abs(Decimal(y) - ref_sin)
            print(f"{k:>3} {angle:>16.1f} {math.ulp(angle):>12.3e} "
                  f"{float(err_c):>12.3e} {float(err_s):>12.3e}")
            if k <= 6:  # 百万圈量级内，误差应在 1e-12 弧度以内
                self.assertLess(float(err_c), 1e-12)
                self.assertLess(float(err_s), 1e-12)

    def test_normalization_error_vs_exact(self):
        # 10^n mod 360 = 280 (n>=3)，用整数算术得到解析真值
        print("\n归一化 10^n 度的误差（解析真值=280）:")
        print(f"{'n':>3} {'ulp(10^n)':>14} {'归一化结果':>22} {'绝对误差(度)':>14}")
        for n in (3, 6, 10, 15, 16, 18, 20, 22, 23, 24):
            angle = float(10**n)
            exact = (10**n) % 360
            got = normalize(angle)
            err = abs(got - exact)
            print(f"{n:>3} {math.ulp(angle):>14.3e} {got:>22.6f} {err:>14.3e}")
            if n <= 22:  # 10^n 可精确表示，误差必须为 0
                self.assertEqual(err, 0.0)
            else:  # 10^23 起 double 无法精确表示，误差可达 1e7 度量级
                self.assertGreater(err, 1.0)

    def test_error_bound_function(self):
        self.assertEqual(normalization_error_bound(0.0), 0.0)
        self.assertEqual(normalization_error_bound(1e6), math.ulp(1e6))
        self.assertLess(normalization_error_bound(1e6), 1e-9)   # 百万度仍极准
        self.assertGreater(normalization_error_bound(1e16), 1.0)  # 误差已超 1 度
        self.assertGreater(FLOAT64_RELIABLE_LIMIT, 1e18)


class TestRadConversion(unittest.TestCase):
    def test_roundtrip(self):
        for d in (0.0, -0.0, 30.0, -45.5, 180.0, 359.999):
            self.assertAlmostEqual(rad2deg(deg2rad(d)), d, places=12)

    def test_known_values(self):
        self.assertAlmostEqual(deg2rad(180.0), math.pi)
        self.assertAlmostEqual(rad2deg(math.pi / 2), 90.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
