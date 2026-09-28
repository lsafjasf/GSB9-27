"""相似度与距离度量库（仅标准库）。

统一约定
--------
向量输入支持两种形式：
  * 序列（list/tuple）：稠密向量，要求两者等长，否则抛 ValueError；
  * 映射（dict，下标 -> 数值）：稀疏向量，缺失键按 0 处理，两向量按键集并对齐。
集合输入为任意可迭代对象，内部转为 set。

数值稳定性策略：
  * 范数用 math.hypot 计算（内部做缩放，上溢/下溢安全）；
  * 求和用 math.fsum（精确舍入求和，避免灾难性抵消）；
  * 余弦点积若直接相乘溢出，退化为"先归一化再点积"的稳定路径；
  * 任何结果若为非有限值（inf/nan），抛 OverflowError，绝不静默返回无穷大。
"""

import math
from collections.abc import Mapping

__all__ = [
    "cosine_similarity",
    "cosine_distance",
    "euclidean_distance",
    "manhattan_distance",
    "jaccard_similarity",
    "jaccard_distance",
]


# ---------------------------------------------------------------- 输入对齐

def _aligned_pairs(a, b):
    """产出对齐后的 (x, y) 分量对。稀疏输入只产出至少一侧非零的键。"""
    a_map = isinstance(a, Mapping)
    b_map = isinstance(b, Mapping)
    if a_map or b_map:
        if not a_map:
            a = dict(enumerate(a))
        if not b_map:
            b = dict(enumerate(b))
        for key in a.keys() | b.keys():
            yield float(a.get(key, 0.0)), float(b.get(key, 0.0))
    else:
        if len(a) != len(b):
            raise ValueError(
                f"稠密向量长度不一致: {len(a)} != {len(b)}"
            )
        for x, y in zip(a, b):
            yield float(x), float(y)


def _check_finite(value, name):
    if not math.isfinite(value):
        raise OverflowError(f"{name} 结果为非有限值（输入量纲差异过大）")
    return value


# ---------------------------------------------------------------- 余弦

def cosine_similarity(a, b):
    """余弦相似度。

    返回范围：[-1.0, 1.0]。
      1.0 表示方向完全相同，0.0 表示正交，-1.0 表示方向相反。
    零向量约定：任一向量为零向量（范数恰为 0）时返回 0.0（确定结果，不报错）。
    对称：cos(a, b) == cos(b, a)。
    """
    pairs = list(_aligned_pairs(a, b))
    dot_terms = []
    norm_a = 0.0
    norm_b = 0.0
    for x, y in pairs:
        dot_terms.append(x * y)
        norm_a = math.hypot(norm_a, x)
        norm_b = math.hypot(norm_b, y)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    if 1e-150 < norm_a < 1e150 and 1e-150 < norm_b < 1e150:
        # 快速路径：范数处于常规范围时，点积各项 |x*y| <= 1e300 必有限，
        # 连除两次不会溢出/下溢
        dot = math.fsum(dot_terms)
        result = dot / norm_a / norm_b
    else:
        # 极端量纲：先归一化再点积，分量均 <= 1，不会溢出，
        # 也避免 norm_a * norm_b 在极小范数时下溢为 0 导致除零。
        result = math.fsum((x / norm_a) * (y / norm_b) for x, y in pairs)
    # 浮点误差可能使结果略微越界，钳回 [-1, 1]
    return _check_finite(max(-1.0, min(1.0, result)), "cosine_similarity")


def cosine_distance(a, b):
    """余弦距离 = 1 - 余弦相似度。

    返回范围：[0.0, 2.0]。
    零向量约定：任一向量为零向量时返回 1.0。
    注意：余弦距离【不满足】三角不等式，不是严格度量，仅用于排序。
    """
    return 1.0 - cosine_similarity(a, b)


# ---------------------------------------------------------------- 欧氏 / 曼哈顿

def euclidean_distance(a, b):
    """欧氏距离（L2）。

    返回范围：[0.0, +inf)，但实现保证不返回 inf——溢出时抛 OverflowError。
    是严格度量：对称、非负、d(x,x)=0、满足三角不等式。
    用 math.hypot 增量计算，对极大/极小分量缩放安全。
    """
    acc = 0.0
    for x, y in _aligned_pairs(a, b):
        acc = math.hypot(acc, x - y)
    return _check_finite(acc, "euclidean_distance")


def manhattan_distance(a, b):
    """曼哈顿距离（L1）。

    返回范围：[0.0, +inf)，溢出时抛 OverflowError，不返回 inf。
    是严格度量：对称、非负、d(x,x)=0、满足三角不等式。
    """
    total = math.fsum(abs(x - y) for x, y in _aligned_pairs(a, b))
    return _check_finite(total, "manhattan_distance")


# ---------------------------------------------------------------- 集合型

def jaccard_similarity(a, b):
    """Jaccard 相似度 = |A ∩ B| / |A ∪ B|。

    返回范围：[0.0, 1.0]。
      1.0 表示两集合完全相同，0.0 表示无交集。
    空集约定：两集合均为空时返回 1.0（约定"空集与自身最相似"）。
    对称：J(A, B) == J(B, A)。
    """
    set_a = set(a)
    set_b = set(b)
    union = len(set_a | set_b)
    if union == 0:
        return 1.0
    return len(set_a & set_b) / union


def jaccard_distance(a, b):
    """Jaccard 距离 = 1 - Jaccard 相似度。

    返回范围：[0.0, 1.0]。
    是严格度量：对称、非负、d(x,x)=0、满足三角不等式。
    """
    return 1.0 - jaccard_similarity(a, b)
