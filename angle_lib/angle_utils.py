"""angle_utils — 角度归一化、弧度互转与二维坐标旋转（仅标准库）。

约定（零度与负角）
------------------
- 角度单位为“度”，逆时针为正（数学惯例，y 轴向上）。负角 = 顺时针。
- 归一化目标区间为左闭右开 [start, start + period)：
  正好落在区间上边界的值回卷到 start（例如 360 -> 0，180 -> -180）。
- 结果中的零一律为 +0.0（-0.0 被规范化掉），保证 normalize(-0.0) == normalize(0.0)
  且符号位一致，便于直接做 == 比较。
- 非有限输入（inf / nan）抛 ValueError。
- 归一化是幂等的：normalize(normalize(x)) == normalize(x)（有测试断言）。

精度说明
--------
math.fmod 是精确运算，归一化本身不引入新误差；误差来自输入 double 的
表示误差（±0.5 ulp）。当 ulp(angle) 接近或超过所需精度（极端时超过 period，
约 |angle| >= 2**52 * period ≈ 1.6e18 度）时，归一化结果失去意义，
应改用高精度表示（见 normalization_error_bound 的 docstring）。
"""

from __future__ import annotations

import math
from typing import Tuple

__all__ = [
    "normalize",
    "normalize_360",
    "normalize_180",
    "deg2rad",
    "rad2deg",
    "rotate2d",
    "normalization_error_bound",
    "FLOAT64_RELIABLE_LIMIT",
]

#: |angle| 超过该值时，double 的 ulp 已大于一整圈，归一化毫无意义。
FLOAT64_RELIABLE_LIMIT = 360.0 * 2.0**52  # ≈ 1.62e18 度


def normalize(angle: float, start: float = 0.0, period: float = 360.0) -> float:
    """把 angle 归一化到左闭右开区间 [start, start + period)。

    幂等：对任意有限输入，normalize(normalize(x)) == normalize(x)。
    零的约定：结果为零时恒为 +0.0。
    边界约定：恰好等于 start + period 的值回卷为 start。
    """
    angle = float(angle)
    if not math.isfinite(angle):
        raise ValueError(f"angle 必须有限, 得到 {angle!r}")
    if not (math.isfinite(start) and math.isfinite(period) and period > 0.0):
        raise ValueError(f"非法区间: start={start!r}, period={period!r}")

    r = math.fmod(angle - start, period)  # fmod 是精确运算
    if r < 0.0:
        r += period
        if r >= period:  # 极小的负余数 + period 后舍入到 period 本身
            r = 0.0
    result = start + r
    if result >= start + period:  # start + r 的舍入撞上开边界
        result = start
    if result == 0.0:
        result = 0.0  # -0.0 -> +0.0
    return result


def normalize_360(angle: float) -> float:
    """归一化到 [0, 360)。"""
    return normalize(angle, 0.0, 360.0)


def normalize_180(angle: float) -> float:
    """归一化到 [-180, 180)。注意 +180 会被映射到 -180。"""
    return normalize(angle, -180.0, 360.0)


def deg2rad(degrees: float) -> float:
    """度 -> 弧度。"""
    return math.radians(degrees)


def rad2deg(radians: float) -> float:
    """弧度 -> 度。"""
    return math.degrees(radians)


def rotate2d(x: float, y: float, angle_deg: float) -> Tuple[float, float]:
    """把点 (x, y) 绕原点逆时针旋转 angle_deg 度，返回新坐标。

    约定：angle_deg == 0 时严格返回 (x, y)（cos=1, sin=0 精确）；
    负角表示顺时针。实现先把角度归一化到 [0, 360) 再转弧度，
    避免对巨大角度直接求三角函数时依赖 libm 的参数约减。
    """
    theta = math.radians(normalize_360(angle_deg))
    c, s = math.cos(theta), math.sin(theta)
    return (c * x - s * y, s * x + c * y)


def normalization_error_bound(angle: float) -> float:
    """归一化结果的最大固有误差（单位：度），即 ulp(angle)。

    fmod 本身精确，误差全部来自输入 double 的表示误差 ±0.5 ulp；
    若 angle 由多步浮点累加得到，实际误差可能远大于此下界。

    何时应放弃 float64 归一化、改用高精度表示：
    - ulp(angle) 大于你需要的角度精度时（经验法则：
      |angle| > 2**52 * 容差，例如要求 1e-9 度精度则上限约 4e6 度）；
    - 或 |angle| >= FLOAT64_RELIABLE_LIMIT 时结果完全无意义。
    替代方案：每步累加后立即取模；用“整数圈数 + 小数偏移”分离表示；
    或使用 decimal.Decimal / fractions.Fraction 保存角度。
    """
    angle = abs(float(angle))
    if not math.isfinite(angle):
        return math.inf
    if angle == 0.0:
        return 0.0
    return math.ulp(angle)
