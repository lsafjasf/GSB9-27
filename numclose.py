"""numclose: 数值比较与容差判定库（仅标准库）。

核心判定规则（与 ``math.isclose`` 语义一致）::

    |a - b| <= max(rel_tol * max(|a|, |b|), abs_tol)

即“相对容差与绝对容差取较大者”的组合判定：

- 当 ``max(|a|, |b|)`` 较大时，由相对容差主导；
- 当两个数都接近零时，``rel_tol * max(|a|, |b|)`` 趋于 0，
  判定自然退化为纯绝对容差 ``abs_tol``，因此比较接近零的值
  必须显式给出 ``abs_tol``，否则只有完全相等才判定为接近。

可断言的代数性质（对任意有限浮点数 a、b 与合法容差）：

1. 自身相等（自反性）：``isclose(a, a)`` 恒为 True。
2. 对称性：``isclose(a, b) == isclose(b, a)``。
   因为 ``|a - b|`` 与 ``max(|a|, |b|)`` 关于 a、b 对称。
3. 容差单调性：若 ``rel_tol1 <= rel_tol2`` 且 ``abs_tol1 <= abs_tol2``，
   则 ``isclose(a, b, rel_tol1, abs_tol1)`` 为 True 蕴含
   ``isclose(a, b, rel_tol2, abs_tol2)`` 为 True。
   因为判定右端关于两个容差都是单调不减的。

特殊值行为（跨平台一致，不依赖任何平台相关路径）：

- NaN：与任何值（包括自身）都不接近，恒返回 False。
- 无穷：``inf`` 与 ``inf`` 接近；``-inf`` 与 ``-inf`` 接近；
  异号无穷、无穷与任何有限值都不接近。
- 正负零：``+0.0`` 与 ``-0.0`` 视为相等（与 IEEE 754 的 ``==`` 一致），
  在 ``abs_tol >= 0`` 的任何容差下都判定为接近。
- 容差参数：``rel_tol``、``abs_tol`` 必须为非负有限数，
  否则抛出 ``ValueError``（NaN/无穷容差同样拒绝），
  避免不同平台对非法容差给出不同结论。
"""

from __future__ import annotations

import math

__all__ = ["isclose", "tolerance", "abs_error", "rel_error"]

DEFAULT_REL_TOL = 1e-9
DEFAULT_ABS_TOL = 0.0


def _validate_tolerances(rel_tol: float, abs_tol: float) -> None:
    for name, tol in (("rel_tol", rel_tol), ("abs_tol", abs_tol)):
        if not isinstance(tol, (int, float)) or isinstance(tol, bool):
            raise TypeError(f"{name} 必须是实数，得到 {type(tol).__name__}")
        if math.isnan(tol) or math.isinf(tol):
            raise ValueError(f"{name} 必须是有限数，得到 {tol!r}")
        if tol < 0.0:
            raise ValueError(f"{name} 必须非负，得到 {tol!r}")


def isclose(a: float, b: float, *,
            rel_tol: float = DEFAULT_REL_TOL,
            abs_tol: float = DEFAULT_ABS_TOL) -> bool:
    """判定 a 与 b 是否在组合容差内相等。

    规则：``|a - b| <= max(rel_tol * max(|a|, |b|), abs_tol)``。
    特殊值行为见模块文档字符串。
    """
    _validate_tolerances(rel_tol, abs_tol)
    a = float(a)
    b = float(b)

    if math.isnan(a) or math.isnan(b):
        return False
    if a == b:
        # 覆盖：有限值完全相等、同号无穷相等、+0.0 == -0.0。
        return True
    if math.isinf(a) or math.isinf(b):
        # 异号无穷，或无穷与有限值：任何容差都无法弥合。
        return False

    return abs(a - b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)


def tolerance(a: float, b: float, *,
              rel_tol: float = DEFAULT_REL_TOL,
              abs_tol: float = DEFAULT_ABS_TOL) -> float:
    """返回判定 a、b 时实际使用的容差阈值（有限值情形）。

    即 ``max(rel_tol * max(|a|, |b|), abs_tol)``。
    对 NaN/无穷输入抛出 ``ValueError``，因为此时容差阈值无意义。
    """
    _validate_tolerances(rel_tol, abs_tol)
    a = float(a)
    b = float(b)
    if not (math.isfinite(a) and math.isfinite(b)):
        raise ValueError("tolerance() 仅对有限值有定义")
    return max(rel_tol * max(abs(a), abs(b)), abs_tol)


def abs_error(a: float, b: float) -> float:
    """绝对误差 ``|a - b|``。结果可能为 inf（如大数异号相减溢出）。"""
    return abs(float(a) - float(b))


def rel_error(a: float, b: float) -> float:
    """相对误差 ``|a - b| / max(|a|, |b|)``。

    - 两者都为零时定义为 0.0（无误差）；
    - 一个为零另一个非零时定义为 inf（相对误差无界）；
    - 使用 ``max(|a|, |b|)`` 作分母，保证关于 a、b 对称，
      与 :func:`isclose` 的判定口径一致。
    """
    a = float(a)
    b = float(b)
    if math.isnan(a) or math.isnan(b):
        return math.nan
    if a == b:
        return 0.0
    if math.isinf(a) or math.isinf(b):
        return math.inf
    scale = max(abs(a), abs(b))
    if scale == 0.0:
        return 0.0
    return abs(a - b) / scale
