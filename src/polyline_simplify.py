"""折线简化库：Ramer-Douglas-Peucker（按偏差阈值递归细分）。

仅依赖 Python 标准库。

功能：
- simplify(): 按偏差阈值递归细分，返回保留点索引与每次决策的偏差值。
- 闭合折线：起点必保留；以最远点拆环为两条开链分别简化。
- find_self_intersections(): 检测简化结果的自交并报告。
- verify_analytic() / verify_sampling(): 两种方式的偏差上界验证。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple, Optional

Point = Tuple[float, float]

EPS = 1e-12


# ---------------------------------------------------------------------------
# 基础几何
# ---------------------------------------------------------------------------

def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    """点 p 到线段 ab 的欧氏距离（解析式）。"""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < EPS:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _dist_sq(p: Point, q: Point) -> float:
    return (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2


def _orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    return (min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS and
            min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS)


def segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    """判断线段 p1p2 与 p3p4 是否相交（含共线重叠与端点接触）。"""
    d1 = _orient(p3, p4, p1)
    d2 = _orient(p3, p4, p2)
    d3 = _orient(p1, p2, p3)
    d4 = _orient(p1, p2, p4)
    if ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and \
       ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS)):
        return True
    if abs(d1) <= EPS and _on_segment(p3, p4, p1):
        return True
    if abs(d2) <= EPS and _on_segment(p3, p4, p2):
        return True
    if abs(d3) <= EPS and _on_segment(p1, p2, p3):
        return True
    if abs(d4) <= EPS and _on_segment(p1, p2, p4):
        return True
    return False



# ---------------------------------------------------------------------------
# 均匀网格索引：加速「点到折线最小距离」查询（用于采样验证）
# ---------------------------------------------------------------------------

class _SegmentGrid:
    """把线段按包围盒插入均匀网格，按 Chebyshev 环扩张做精确最近查询。"""

    def __init__(self, segs: List[Tuple[Point, Point]]):
        self.segs = segs
        xs = [p[0] for seg in segs for p in seg]
        ys = [p[1] for seg in segs for p in seg]
        self.x0, self.y0 = min(xs), min(ys)
        extent = max(max(xs) - self.x0, max(ys) - self.y0, EPS)
        # 目标：每格平均常数条线段
        self.cell = extent / max(1.0, math.sqrt(len(segs)))
        self.grid: Dict[Tuple[int, int], List[int]] = {}
        for idx, (a, b) in enumerate(segs):
            ax, ay = a
            bx, by = b
            lo_x = int((min(ax, bx) - self.x0) // self.cell)
            hi_x = int((max(ax, bx) - self.x0) // self.cell)
            lo_y = int((min(ay, by) - self.y0) // self.cell)
            hi_y = int((max(ay, by) - self.y0) // self.cell)
            for cx in range(lo_x, hi_x + 1):
                for cy in range(lo_y, hi_y + 1):
                    self.grid.setdefault((cx, cy), []).append(idx)

    def _cell_xy(self, x: float, y: float) -> Tuple[int, int]:
        return (int((x - self.x0) // self.cell),
                int((y - self.y0) // self.cell))

    def min_distance(self, p: Point) -> float:
        cx, cy = self._cell_xy(p[0], p[1])
        best = float("inf")
        ring = 0
        while True:
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    if max(abs(dx), abs(dy)) != ring:
                        continue
                    bucket = self.grid.get((cx + dx, cy + dy))
                    if not bucket:
                        continue
                    for idx in bucket:
                        a, b = self.segs[idx]
                        d = point_segment_distance(p, a, b)
                        if d < best:
                            best = d
            # 下一环（ring+1）到 p 的距离下界为 ring*cell；已不小于 best 即可停
            if ring * self.cell >= best:
                break
            ring += 1
        return best


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    """一次递归细分决策：考察子链 [start, end]，最大偏差点为 split，
    其到弦 start-end 的偏差为 deviation；deviation > tol 时保留 split。"""
    start: int
    end: int
    split: Optional[int]
    deviation: float
    kept: bool


@dataclass
class SimplifyResult:
    indices: List[int]                 # 保留点的原始索引（升序）
    decisions: List[Decision]          # 每次递归决策及偏差值
    closed: bool
    tolerance: float
    self_intersections: List[Tuple[int, int]] = field(default_factory=list)
    # 被删除点 -> 见证弦（简化折线的真实线段端点的原始索引）
    leaf: dict = field(default_factory=dict)

    @property
    def compression_ratio(self) -> float:
        return len(self.indices) / self._n_total if self._n_total else 1.0

    _n_total: int = 0


# ---------------------------------------------------------------------------
# 核心：RDP 递归细分（显式栈实现，语义与递归一致，避免深递归溢出）
# ---------------------------------------------------------------------------

def _rdp(points: Sequence[Point], seq: List[int], tol: float,
         kept: set, decisions: List[Decision],
         leaf: Dict[int, Tuple[int, int]]) -> None:
    """对全局索引序列 seq（端点已保留）做 RDP，记录每次决策。

    leaf: 对被删除点 k，记录其所属叶决策的弦 (seq[i], seq[j])。
    该弦必为简化折线的一条真实线段，可作为偏差见证段。
    """
    stack = [(0, len(seq) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a = points[seq[i]]
        b = points[seq[j]]
        dmax = -1.0
        kmax = -1
        for k in range(i + 1, j):
            d = point_segment_distance(points[seq[k]], a, b)
            if d > dmax:
                dmax = d
                kmax = k
        keep = dmax > tol
        decisions.append(Decision(seq[i], seq[j],
                                  seq[kmax] if keep else None,
                                  dmax, keep))
        if keep:
            kept.add(seq[kmax])
            stack.append((i, kmax))
            stack.append((kmax, j))
        else:
            for k in range(i + 1, j):
                leaf[seq[k]] = (seq[i], seq[j])


def _is_closed(points: Sequence[Point]) -> bool:
    return (len(points) > 2 and
            abs(points[0][0] - points[-1][0]) <= EPS and
            abs(points[0][1] - points[-1][1]) <= EPS)


def simplify(points: Sequence[Point], tolerance: float,
             closed: Optional[bool] = None) -> SimplifyResult:
    """按偏差阈值简化折线。

    points:    [(x, y), ...]
    tolerance: 偏差阈值（>= 0）。被删除点到保留折线的距离保证不超过它。
    closed:    是否按闭合折线处理；None 时自动判断（首末点重合即闭合）。

    返回 SimplifyResult，含保留点索引、每次决策的偏差值、自交报告。
    """
    if tolerance < 0:
        raise ValueError("tolerance must be >= 0")
    pts = [(float(x), float(y)) for x, y in points]
    n = len(pts)
    decisions: List[Decision] = []

    if closed is None:
        closed = _is_closed(pts)

    # 退化情形：0/1/2 个点全部保留
    if n <= 2:
        res = SimplifyResult(list(range(n)), decisions, closed, tolerance)
        res._n_total = n
        return res

    leaf: Dict[int, Tuple[int, int]] = {}
    if not closed:
        kept = {0, n - 1}
        _rdp(pts, list(range(n)), tolerance, kept, decisions, leaf)
        indices = sorted(kept)
        simplified = [pts[i] for i in indices]
        res = SimplifyResult(indices, decisions, False, tolerance)
        res._n_total = n
        res.leaf = leaf
        res.self_intersections = find_self_intersections(simplified, closed=False)
        return res

    # 闭合折线：去掉重复的末点，得到环
    ring = pts[:-1] if _is_closed(pts) else list(pts)
    m = len(ring)
    if m < 3:
        res = SimplifyResult(list(range(m)), decisions, True, tolerance)
        res._n_total = n
        return res

    # 起点（索引 0）必保留；取离起点最远的点 far 把环拆成两条开链
    far = max(range(m), key=lambda k: _dist_sq(ring[k], ring[0]))
    kept = {0, far}
    _rdp(ring, list(range(0, far + 1)), tolerance, kept, decisions, leaf)
    _rdp(ring, list(range(far, m)) + [0], tolerance, kept, decisions, leaf)
    indices = sorted(kept)
    simplified = [ring[i] for i in indices]
    res = SimplifyResult(indices, decisions, True, tolerance)
    res._n_total = n
    res.leaf = leaf
    res.self_intersections = find_self_intersections(simplified, closed=True)
    return res


# ---------------------------------------------------------------------------
# 自交检测
# ---------------------------------------------------------------------------

def find_self_intersections(pts: Sequence[Point],
                            closed: bool = False) -> List[Tuple[int, int]]:
    """检测折线自交，返回相交线段对的下标 (i, j)（i、j 为线段编号）。

    相邻线段共享端点不算自交；闭合折线的首尾线段相邻也不算。
    用均匀网格做宽相筛选，避免 O(n^2) 全对比较。
    """
    pts = list(pts)
    segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    if closed and len(pts) > 2:
        segs.append((pts[-1], pts[0]))
    n_seg = len(segs)
    if n_seg < 2:
        return []

    xs = [p[0] for seg in segs for p in seg]
    ys = [p[1] for seg in segs for p in seg]
    x0, y0 = min(xs), min(ys)
    extent = max(max(xs) - x0, max(ys) - y0, EPS)
    cell = extent / max(1.0, math.sqrt(n_seg))
    grid: Dict[Tuple[int, int], List[int]] = {}
    for idx, (a, b) in enumerate(segs):
        lo_x = int((min(a[0], b[0]) - x0) // cell)
        hi_x = int((max(a[0], b[0]) - x0) // cell)
        lo_y = int((min(a[1], b[1]) - y0) // cell)
        hi_y = int((max(a[1], b[1]) - y0) // cell)
        for cx in range(lo_x, hi_x + 1):
            for cy in range(lo_y, hi_y + 1):
                grid.setdefault((cx, cy), []).append(idx)

    hits: List[Tuple[int, int]] = []
    seen = set()
    for bucket in grid.values():
        m = len(bucket)
        for bi in range(m):
            i = bucket[bi]
            for bj in range(bi + 1, m):
                j = bucket[bj]
                if j <= i + 1:
                    continue  # 相邻或重复
                if closed and i == 0 and j == n_seg - 1:
                    continue
                key = (i, j)
                if key in seen:
                    continue
                seen.add(key)
                if segments_intersect(segs[i][0], segs[i][1],
                                      segs[j][0], segs[j][1]):
                    hits.append(key)
    hits.sort()
    return hits


# ---------------------------------------------------------------------------
# 偏差上界验证
# ---------------------------------------------------------------------------

def _simplified_segments(points: Sequence[Point], indices: List[int],
                         closed: bool) -> List[Tuple[Point, Point]]:
    sp = [points[i] for i in indices]
    segs = [(sp[i], sp[i + 1]) for i in range(len(sp) - 1)]
    if closed and len(sp) > 2:
        segs.append((sp[-1], sp[0]))
    return segs


def _distance_to_polyline(p: Point, segs: List[Tuple[Point, Point]]) -> float:
    return min(point_segment_distance(p, a, b) for a, b in segs)


def verify_analytic(points: Sequence[Point], indices: List[int],
                    tolerance: float, closed: bool = False,
                    leaf: Optional[dict] = None):
    """解析验证：逐个计算每个被删除点到保留折线的距离。

    若提供 leaf（simplify 结果中的见证弦表），则每个被删除点直接
    对其见证弦（简化折线的真实线段）求精确距离，复杂度 O(n)，
    且该距离是「到折线最小距离」的上界，足以验证偏差保证。
    否则暴力对该点到所有保留线段取最小值。

    返回 (ok, max_deviation, worst_index)。
    """
    pts = [(float(x), float(y)) for x, y in points]
    if closed and _is_closed(pts):
        pts = pts[:-1]
    kept = set(indices)
    grid = None if leaf else _SegmentGrid(
        _simplified_segments(pts, indices, closed))
    max_dev = 0.0
    worst = -1
    for i, p in enumerate(pts):
        if i in kept:
            continue
        if leaf is not None:
            a_i, b_i = leaf[i]
            d = point_segment_distance(p, pts[a_i], pts[b_i])
        else:
            d = grid.min_distance(p)
        if d > max_dev:
            max_dev = d
            worst = i
    ok = max_dev <= tolerance + 1e-9
    return ok, max_dev, worst


def verify_sampling(points: Sequence[Point], indices: List[int],
                    tolerance: float, closed: bool = False,
                    samples_per_segment: int = 64,
                    leaf: Optional[dict] = None):  # 保留参数位，兼容旧调用
    """采样验证：沿原折线每条边均匀采样（含顶点间的插值点），
    计算采样点到保留折线的距离，取最大值。

    返回 (ok, max_deviation, n_samples)。
    """
    pts = [(float(x), float(y)) for x, y in points]
    if closed and _is_closed(pts):
        pts = pts[:-1]
    segs = _simplified_segments(pts, indices, closed)
    grid = _SegmentGrid(segs)
    ring = list(pts)
    if closed:
        ring = ring + [ring[0]]
    max_dev = 0.0
    n_samples = 0
    for i in range(len(ring) - 1):
        a, b = ring[i], ring[i + 1]
        for k in range(samples_per_segment + 1):
            t = k / samples_per_segment
            p = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
            d = grid.min_distance(p)
            n_samples += 1
            if d > max_dev:
                max_dev = d
    ok = max_dev <= tolerance + 1e-9
    return ok, max_dev, n_samples
