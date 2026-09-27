"""相似度与距离度量库（仅依赖 Python 标准库）。

支持两种向量表示：
  * 稠密：实数序列（list / tuple），按位置对齐，长度必须相同；
  * 稀疏：键 -> 实数的映射（dict），缺失键按 0 处理，键集合即维度。

设计要点
--------
所有数值内核均为「缩放」算法（blue / scaled hypot 与单位分量点积）：
计算过程中只出现除法与不超过最大输入绝对值的量，因此在输入有限的前提下
不会产生除零、inf 或 NaN。无效输入会被明确拒绝：

  * NaN                 -> ValueError
  * inf / -inf          -> OverflowError
  * 稠密长度不一致       -> ValueError
  * 稠密与稀疏混用       -> TypeError
  * 零向量上的余弦       -> 默认 ValueError（on_zero="zero" 时按约定返回）

约定：数学函数永远返回普通 float；任何错误都以异常显式抛出。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Union

__all__ = [
    "cosine_similarity",
    "cosine_distance",
    "angular_distance",
    "euclidean_distance",
    "manhattan_distance",
    "jaccard_similarity",
    "jaccard_distance",
    "dice_similarity",
    "overlap_coefficient",
]

Pair = Union[Sequence[float], Mapping]

_EPS = 1e-300  # 小于该范数视为零向量（double 下 1e-300 已接近最小正规数）


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _reject_nonfinite(x: float, where: str) -> None:
    if math.isnan(x):
        raise ValueError(f"NaN 不允许出现在{where}")
    if math.isinf(x):
        raise OverflowError(f"inf/-inf 不允许出现在{where}")


def _kind(v, name: str) -> str:
    if isinstance(v, Mapping):
        return "sparse"
    if isinstance(v, Sequence) and not isinstance(v, (str, bytes)):
        return "dense"
    raise TypeError(f"{name} 必须是实数序列或映射，得到 {type(v).__name__}")


def _check_pair(a, b) -> str:
    ka, kb = _kind(a, "a"), _kind(b, "b")
    if ka != kb:
        raise TypeError(f"a 是{ka}而 b 是{kb}，稠密与稀疏表示不能混用")
    return ka


def _scaled_add(scale: float, s: float, ax: float):
    """scaled hypot 累加：返回 (新最大轴, 缩放平方和)。"""
    if ax > scale:
        s = 1.0 + s * (scale / ax) ** 2
        scale = ax
    elif ax:
        s += (ax / scale) ** 2
    return scale, s


def _dense_norm(v) -> float:
    scale, s = 0.0, 1.0
    for x in v:
        _reject_nonfinite(x, "向量")
        scale, s = _scaled_add(scale, s, x if x >= 0.0 else -x)
    return 0.0 if scale == 0.0 else scale * math.sqrt(s)


def _sparse_norm(v) -> float:
    scale, s = 0.0, 1.0
    for x in v.values():
        _reject_nonfinite(x, "向量")
        scale, s = _scaled_add(scale, s, x if x >= 0.0 else -x)
    return 0.0 if scale == 0.0 else scale * math.sqrt(s)


def _dense_diff_norm(a, b) -> float:
    if len(a) != len(b):
        raise ValueError(f"稠密向量长度不一致：{len(a)} != {len(b)}")
    scale, s = 0.0, 1.0
    for x, y in zip(a, b):
        _reject_nonfinite(x, "向量")
        _reject_nonfinite(y, "向量")
        d = x - y
        scale, s = _scaled_add(scale, s, d if d >= 0.0 else -d)
    return 0.0 if scale == 0.0 else scale * math.sqrt(s)


def _sparse_keys(a, b):
    try:
        return a.keys() | b.keys()
    except TypeError:  # 非常规 Mapping 实现
        return set(a) | set(b)


def _sparse_diff_norm(a, b) -> float:
    scale, s = 0.0, 1.0
    for k in _sparse_keys(a, b):
        x = a.get(k, 0.0)
        y = b.get(k, 0.0)
        _reject_nonfinite(x, "向量")
        _reject_nonfinite(y, "向量")
        d = x - y
        scale, s = _scaled_add(scale, s, d if d >= 0.0 else -d)
    return 0.0 if scale == 0.0 else scale * math.sqrt(s)


def _dense_manhattan(a, b) -> float:
    if len(a) != len(b):
        raise ValueError(f"稠密向量长度不一致：{len(a)} != {len(b)}")
    total = math.fsum(
        (lambda d: d if d >= 0.0 else -d)(x - y) for x, y in zip(a, b)
    )
    for x, y in zip(a, b):
        _reject_nonfinite(x, "向量")
        _reject_nonfinite(y, "向量")
    if not math.isfinite(total):
        raise OverflowError("曼哈顿距离超出 float 范围")
    return total


def _sparse_manhattan(a, b) -> float:
    parts = []
    for k in _sparse_keys(a, b):
        x = a.get(k, 0.0)
        y = b.get(k, 0.0)
        _reject_nonfinite(x, "向量")
        _reject_nonfinite(y, "向量")
        d = x - y
        parts.append(d if d >= 0.0 else -d)
    total = math.fsum(parts)
    if not math.isfinite(total):
        raise OverflowError("曼哈顿距离超出 float 范围")
    return total


def _unit_dot_dense(a, b, na: float, nb: float) -> float:
    """单位向量点积：每项绝对值 <= 1，绝不溢出。"""
    return math.fsum((x / na) * (y / nb) for x, y in zip(a, b))


def _unit_dot_sparse(a, b, na: float, nb: float) -> float:
    if len(a) > len(b):
        a, b = b, a
    return math.fsum(
        (x / na) * (b[k] / nb) for k, x in a.items() if k in b
    )


def _zero_policy(na: float, nb: float, on_zero: str, zero_value: float):
    if on_zero == "raise":
        raise ValueError(
            f"余弦在零向量上没有定义（范数 {na:g}, {nb:g}）；"
            "如需约定值请传 on_zero=\"zero\""
        )
    if on_zero != "zero":
        raise ValueError("on_zero 只能是 \"raise\" 或 \"zero\"")
    return zero_value


def _norm(kind: str, v) -> float:
    return _dense_norm(v) if kind == "dense" else _sparse_norm(v)


def _diff_norm(kind: str, a, b) -> float:
    return _dense_diff_norm(a, b) if kind == "dense" else _sparse_diff_norm(a, b)


def _manhattan(kind: str, a, b) -> float:
    return _dense_manhattan(a, b) if kind == "dense" else _sparse_manhattan(a, b)


def _as_set(x, name: str):
    if isinstance(x, (set, frozenset)):
        return x
    if isinstance(x, (list, tuple)):
        return set(x)
    raise TypeError(f"{name} 必须是 set/frozenset/list/tuple")


# --------------------------------------------------------------------------- #
# 余弦族
# --------------------------------------------------------------------------- #
def cosine_similarity(a, b, *, on_zero: str = "raise") -> float:
    r"""余弦相似度  a·b / (‖a‖₂‖b‖₂)。

    范围 [-1, 1]：1 同向，0 正交，-1 反向。零向量上数学未定义，
    默认抛 ValueError；on_zero="zero" 时约定返回 0.0。
    对非零向量保证有界且有限（含 1e-300 量级、1e300 量级）。
    """
    kind = _check_pair(a, b)
    na, nb = _norm(kind, a), _norm(kind, b)
    if na < _EPS or nb < _EPS:
        return _zero_policy(na, nb, on_zero, 0.0)
    dot = (
        _unit_dot_dense(a, b, na, nb)
        if kind == "dense"
        else _unit_dot_sparse(a, b, na, nb)
    )
    if not math.isfinite(dot):  # 理论不可达，防御性检查
        raise OverflowError("余弦点积超出 float 范围")
    return min(1.0, max(-1.0, dot))


def cosine_distance(a, b, *, on_zero: str = "raise") -> float:
    """余弦距离 1 - 余弦相似度。范围 [0, 2]，自距离为 0。

    注意：它不是度量（不满足三角不等式）；需要度量时用 angular_distance。
    """
    if on_zero == "zero":
        kind = _check_pair(a, b)
        na, nb = _norm(kind, a), _norm(kind, b)
        if na < _EPS or nb < _EPS:
            return 1.0
    return 1.0 - cosine_similarity(a, b, on_zero=on_zero)


def angular_distance(a, b, *, on_zero: str = "raise") -> float:
    """角距离 arccos(余弦) / π。范围 [0, 1]，是真正的度量（满足三角不等式）。

    零向量上方向未定义，默认抛 ValueError；on_zero="zero" 时返回 1.0
    （把零向量约定为与一切方向相距 π）。
    """
    kind = _check_pair(a, b)
    na, nb = _norm(kind, a), _norm(kind, b)
    if na < _EPS or nb < _EPS:
        return _zero_policy(na, nb, on_zero, 1.0)
    cos = cosine_similarity(a, b, on_zero=on_zero)
    return math.acos(min(1.0, max(-1.0, cos))) / math.pi


# --------------------------------------------------------------------------- #
# Lp 距离
# --------------------------------------------------------------------------- #
def euclidean_distance(a, b) -> float:
    r"""欧氏（L2）距离 ‖a-b‖₂。范围 [0, +∞)，是度量；零向量合法。"""
    kind = _check_pair(a, b)
    return _diff_norm(kind, a, b)


def manhattan_distance(a, b) -> float:
    r"""曼哈顿（L1）距离 Σ|a_i-b_i|。范围 [0, +∞)，是度量；零向量合法。

    使用 math.fsum 求和；总和超出 float 范围时抛 OverflowError。
    """
    kind = _check_pair(a, b)
    return _manhattan(kind, a, b)


# --------------------------------------------------------------------------- #
# 集合型相似度
# --------------------------------------------------------------------------- #
def jaccard_similarity(a, b) -> float:
    r"""Jaccard 相似度 |A∩B| / |A∪B|。范围 [0, 1]。

    约定：两个空集返回 1.0（同一对象）。自相似度为 1。
    """
    sa, sb = _as_set(a, "a"), _as_set(b, "b")
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def jaccard_distance(a, b) -> float:
    """Jaccard 距离 1 - Jaccard。范围 [0, 1]，是有限集合上的度量。"""
    return 1.0 - jaccard_similarity(a, b)


def dice_similarity(a, b) -> float:
    r"""Sørensen–Dice 系数 2|A∩B| / (|A|+|B|)。范围 [0, 1]。

    约定：两个空集返回 1.0。注意 Dice 不满足三角不等式，不是度量。
    """
    sa, sb = _as_set(a, "a"), _as_set(b, "b")
    denom = len(sa) + len(sb)
    if denom == 0:
        return 1.0
    return 2.0 * len(sa & sb) / denom


def overlap_coefficient(a, b) -> float:
    r"""重叠系数 |A∩B| / min(|A|, |B|)。范围 [0, 1]。

    约定：两个空集返回 1.0，仅一个为空返回 0.0。
    """
    sa, sb = _as_set(a, "a"), _as_set(b, "b")
    smaller = min(len(sa), len(sb))
    if smaller == 0:
        return 1.0 if not sa and not sb else 0.0
    return len(sa & sb) / smaller
