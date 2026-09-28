"""打印归一化与旋转的精度评估表。运行: python3 precision_report.py"""

import math
from decimal import Decimal, getcontext

from angle_utils import normalize, normalization_error_bound, rotate2d

getcontext().prec = 60
PI_DEC = Decimal(
    "3.141592653589793238462643383279502884197169399375105820974944592307816"
)


def dec_sin_cos(deg: Decimal):
    deg = deg % Decimal(360)
    x = deg * PI_DEC / Decimal(180)
    sin_x, cos_x = Decimal(0), Decimal(1)
    term_s, term_c = x, Decimal(1)
    n = 1
    while True:
        sin_x += term_s
        term_s = -term_s * x * x / ((2 * n) * (2 * n + 1))
        term_c = -term_c * x * x / ((2 * n - 1) * (2 * n))
        cos_x += term_c
        n += 1
        if abs(term_s) < Decimal("1e-55") and abs(term_c) < Decimal("1e-55"):
            return +sin_x, +cos_x


def main():
    print("== 表1: 归一化 10^n 度（解析真值 = 10^n mod 360，n>=3 时恒为 280）==")
    print(f"{'n':>3} {'ulp(10^n) 度':>14} {'归一化结果':>22} {'绝对误差(度)':>14}")
    for n in list(range(3, 13)) + [15, 16, 18, 20, 22, 23, 24]:
        angle = float(10**n)
        exact = (10**n) % 360
        got = normalize(angle)
        print(f"{n:>3} {math.ulp(angle):>14.3e} {got:>22.6f} {abs(got - exact):>14.3e}")

    print()
    print("== 表2: 旋转 (1,0) 转 30° + 360°·10^k，与 60 位 Decimal 参考值比较 ==")
    ref_sin, ref_cos = dec_sin_cos(Decimal(30))
    print(f"{'k':>3} {'角度(度)':>18} {'ulp(角度)':>12} {'|Δcos|':>12} {'|Δsin|':>12}")
    for k in range(0, 14):
        angle = 30.0 + 360.0 * 10**k
        x, y = rotate2d(1.0, 0.0, angle)
        print(f"{k:>3} {angle:>18.1f} {math.ulp(angle):>12.3e} "
              f"{float(abs(Decimal(x) - ref_cos)):>12.3e} "
              f"{float(abs(Decimal(y) - ref_sin)):>12.3e}")

    print()
    print("== 表3: 给定精度要求下，float64 能安全表达的最大角度 ==")
    print(f"{'所需精度(度)':>14} {'最大 |angle| (度)':>18}")
    for tol in (1e-3, 1e-6, 1e-9, 1e-12, 1e-15):
        print(f"{tol:>14.0e} {2.0**52 * tol:>18.3e}")
    print()
    print(f"ulp(angle) 超过一整圈（归一化彻底无意义）的阈值: "
          f"{360.0 * 2.0**52:.3e} 度")
    print(f"示例: normalization_error_bound(1e6)  = {normalization_error_bound(1e6):.3e} 度")
    print(f"      normalization_error_bound(1e16) = {normalization_error_bound(1e16):.3e} 度")


if __name__ == "__main__":
    main()
