"""折线简化库：Ramer-Douglas-Peucker 按偏差阈值递归细分算法（仅标准库）。

特性：
- 输出保留点索引与每一次细分决策的偏差值（Decision）。
- 支持闭合折线：起点（索引 0）强制保留，结果做自交检测并报告。
- 偏差保证：任一被删除点到其被替换弦的距离 <= epsilon，
  因而到最终保留折线的距离也 <= epsilon（verify.py 提供两种验证）。
"""

from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

Point = Tuple[float, float]


@dataclass
class Decision:
    """一次递归细分决策。"""
    seg_start: int      # 当前弦起点（原始点索引）
    seg_end: int        # 当前弦终点（原始点索引）
    split_index: int    # 弦内偏差最大的点（-1 表示弦内无点）
    deviation: float    # 该点到弦的距离（本次决策的偏差值）
    split: bool         # True=偏差>阈值，保留 split_index 并继续细分；False=接受该弦


@dataclass
class SimplifyResult:
    kept_indices: List[int]                 # 保留点的原始索引（升序）
    decisions: List[Decision]               # 全部决策记录
    epsilon: float
    closed: bool
    self_intersections: List[Tuple[int, int]] = field(default_factory=list)  # 自交段对


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    """点 p 到线段 ab 的精确距离。"""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def _rdp(points, lo, hi, epsilon, keep, decisions, index_map=None):
    """对 points[lo..hi] 做递归细分（用显式栈避免递归深度问题）。

    index_map: 将局部索引映射回原始索引（用于闭合折线的第二条链）。
    """
    def orig(i):
        return index_map[i] if index_map is not None else i

    keep[lo] = True
    keep[hi] = True
    stack = [(lo, hi)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        a, b = points[lo], points[hi]
        max_dev = -1.0
        split_k = -1
        for k in range(lo + 1, hi):
            d = point_segment_distance(points[k], a, b)
            if d > max_dev:
                max_dev = d
                split_k = k
        do_split = max_dev > epsilon
        decisions.append(Decision(orig(lo), orig(hi), orig(split_k), max_dev, do_split))
        if do_split:
            keep[split_k] = True
            stack.append((lo, split_k))
            stack.append((split_k, hi))


def simplify(points, epsilon, closed=False, avoid_self_intersection=False,
             max_retries=8):
    """简化折线，返回 SimplifyResult（保留索引 + 决策记录）。

    closed=True 时输入为闭合环（首尾点可重复也可不重复，自动处理），
    索引 0 强制保留；简化后检测自交并记录在结果中。
    avoid_self_intersection=True 时若出现自交，自动将阈值减半重试。
    """
    pts = [(float(p[0]), float(p[1])) for p in points]
    if closed and len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]  # 去掉重复的收尾点，按环处理

    eps = float(epsilon)
    result = None
    for _ in range(max_retries + 1):
        result = _simplify_once(pts, eps, closed)
        if not (closed and avoid_self_intersection and result.self_intersections):
            return result
        eps /= 2.0
    return result


def _simplify_once(pts, epsilon, closed):
    n = len(pts)
    decisions = []
    if n == 0:
        return SimplifyResult([], decisions, epsilon, closed)
    if not closed:
        keep = [False] * n
        if n >= 2:
            _rdp(pts, 0, n - 1, epsilon, keep, decisions)
        else:
            keep[0] = True
        kept = [i for i, k in enumerate(keep) if k]
        return SimplifyResult(kept, decisions, epsilon, closed=False)

    # 闭合环：取离起点最远的点 m 作为对偶锚点，拆成两条开链分别细分，
    # 保证索引 0 保留且两条链的端点一致。
    if n < 3:
        return SimplifyResult(list(range(n)), decisions, epsilon, closed=True)
    ax, ay = pts[0]
    m = max(range(n), key=lambda i: (pts[i][0] - ax) ** 2 + (pts[i][1] - ay) ** 2)
    if m == 0:
        m = n // 2

    keep_a = [False] * (m + 1)
    _rdp(pts[:m + 1], 0, m, epsilon, keep_a, decisions)

    chain_b = pts[m:] + [pts[0]]
    index_map_b = list(range(m, n)) + [0]
    keep_b = [False] * len(chain_b)
    _rdp(chain_b, 0, len(chain_b) - 1, epsilon, keep_b, decisions, index_map_b)

    kept_set = {i for i, k in enumerate(keep_a) if k}
    kept_set |= {index_map_b[i] for i, k in enumerate(keep_b) if k}
    kept = sorted(kept_set)

    simplified_ring = [pts[i] for i in kept]
    intersections = find_self_intersections(simplified_ring, closed=True)
    return SimplifyResult(kept, decisions, epsilon, closed=True,
                          self_intersections=intersections)


# ---------------- 自交检测 ----------------

def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, c):
    return (min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and
            min(a[1], b[1]) <= c[1] <= max(a[1], b[1]))


def segments_intersect(p1, p2, p3, p4):
    """判断线段 p1p2 与 p3p4 是否相交（含共线重叠/端点接触）。"""
    d1 = _orient(p3, p4, p1)
    d2 = _orient(p3, p4, p2)
    d3 = _orient(p1, p2, p3)
    d4 = _orient(p1, p2, p4)
    if d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0 and \
            ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    if d1 == 0 and _on_segment(p3, p4, p1):
        return True
    if d2 == 0 and _on_segment(p3, p4, p2):
        return True
    if d3 == 0 and _on_segment(p1, p2, p3):
        return True
    if d4 == 0 and _on_segment(p1, p2, p4):
        return True
    return False


def find_self_intersections(poly, closed=False):
    """检测折线自交，返回相交的段索引对 (i, j)（i<j，跳过相邻段）。"""
    m = len(poly)
    if m < 3:
        return []
    seg_count = m if closed else m - 1
    result = []
    for i in range(seg_count):
        a1, a2 = poly[i], poly[(i + 1) % m]
        for j in range(i + 1, seg_count):
            if j == i + 1:
                continue
            if closed and i == 0 and j == seg_count - 1:
                continue  # 闭合环的首尾段相邻
            b1, b2 = poly[j], poly[(j + 1) % m]
            if segments_intersect(a1, a2, b1, b2):
                result.append((i, j))
    return result
