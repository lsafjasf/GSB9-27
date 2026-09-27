"""KD-Tree：支持插入、删除、Top-K 最近邻查询（仅标准库）。

设计要点
--------
* 空间划分：每个内部节点按某一坐标轴把空间切成两半，查询时利用
  “分割超平面到查询点的距离”做剪枝（见 query 的注释）。
* 平衡：插入时做 scapegoat 式局部重建（任一子树占比 > REBALANCE_RATIO
  即重建该子树），均摊保持 O(log n) 深度。
* 删除：墓碑（lazy delete）。节点仅标记为 dead，查询时跳过；
  当墓碑过多或局部重建时统一回收。删除不影响查询正确性。
* 决胜规则（确定性）：每个点插入时分配唯一递增 pid；
  排序键为 (欧氏距离平方, pid)，距离并列时 pid 小者排前。
  暴力扫描使用完全相同的排序键，因此二者结果逐条一致。
* 距离：内部一律用欧氏距离平方比较（单调等价，避免开方），
  返回结果时再开方为真实欧氏距离。
"""

from __future__ import annotations

import heapq
import math
from typing import Hashable, Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, ...]
# 查询结果项：(pid, point, euclidean_distance)
Result = Tuple[int, Point, float]


def dist_sq(a: Sequence[float], b: Sequence[float]) -> float:
    """欧氏距离平方（暴力扫描对拍时也用它，保证排序键一致）。"""
    s = 0.0
    for x, y in zip(a, b):
        d = x - y
        s += d * d
    return s


class _Node:
    __slots__ = ("point", "pid", "axis", "left", "right", "size", "dead")

    def __init__(self, point: Point, pid: int, axis: int) -> None:
        self.point = point
        self.pid = pid
        self.axis = axis
        self.left: Optional[_Node] = None
        self.right: Optional[_Node] = None
        self.size = 1          # 子树节点总数（含墓碑，用于平衡判断）
        self.dead = False


class KDTree:
    REBALANCE_RATIO = 0.75     # 子树占比超过该值即局部重建
    TOMBSTONE_FACTOR = 1.0     # 墓碑数 > 存活数 * 该值 且超过阈值即整体重建
    TOMBSTONE_MIN = 32

    def __init__(self, dim: int) -> None:
        if dim < 1:
            raise ValueError("dim must be >= 1")
        self.dim = dim
        self._root: Optional[_Node] = None
        self._nodes: dict[int, _Node] = {}   # pid -> node，删除 O(1) 定位
        self._next_pid = 0
        self._live = 0
        self._dead = 0
        # 最近一次 query 访问的节点数（含墓碑），供剪枝率统计
        self.nodes_visited = 0

    def __len__(self) -> int:
        return self._live

    # ------------------------------------------------------------------ 插入

    def insert(self, point: Sequence[float]) -> int:
        """插入点，返回唯一 pid（决胜规则的一部分）。"""
        pt = self._check(point)
        pid = self._next_pid
        self._next_pid += 1
        node = _Node(pt, pid, 0)
        self._nodes[pid] = node
        if self._root is None:
            self._root = node
        else:
            self._root = self._insert(self._root, node)
        self._live += 1
        return pid

    def _insert(self, cur: _Node, new: _Node) -> _Node:
        # 必须用节点自身记录的分割轴：局部重建后轴是“最大方差轴”，
        # 与按深度轮转得到的轴不一定一致。
        axis = cur.axis
        cur.size += 1
        if new.point[axis] < cur.point[axis]:
            if cur.left is None:
                new.axis = (axis + 1) % self.dim
                cur.left = new
            else:
                cur.left = self._insert(cur.left, new)
        else:  # 相等走右边：重复点全部落在搜索路径上，删除/查找可确定地进行
            if cur.right is None:
                new.axis = (axis + 1) % self.dim
                cur.right = new
            else:
                cur.right = self._insert(cur.right, new)
        if self._unbalanced(cur):
            cur = self._rebuild(cur)
        return cur

    def _unbalanced(self, node: _Node) -> bool:
        limit = self.REBALANCE_RATIO * node.size
        left = node.left.size if node.left else 0
        right = node.right.size if node.right else 0
        return left > limit or right > limit

    # ------------------------------------------------------------------ 删除

    def delete(self, pid: int) -> bool:
        """按 pid 删除（墓碑）。不存在或已删除返回 False。"""
        node = self._nodes.get(pid)
        if node is None or node.dead:
            return False
        node.dead = True
        self._live -= 1
        self._dead += 1
        if (self._dead > self.TOMBSTONE_MIN
                and self._dead > self.TOMBSTONE_FACTOR * max(self._live, 1)):
            if self._root is not None:
                self._root = self._rebuild(self._root)
        return True

    def delete_point(self, point: Sequence[float]) -> bool:
        """删除一个坐标完全相等的存活点（重复点时删 pid 最小者）。"""
        pt = self._check(point)
        node = self._root
        while node is not None:
            if not node.dead and node.point == pt:
                return self.delete(node.pid)
            axis = node.axis
            node = node.left if pt[axis] < node.point[axis] else node.right
        return False

    # ------------------------------------------------------------------ 重建

    def _rebuild(self, root: _Node) -> Optional[_Node]:
        """把子树重建成平衡树（按最大方差轴取中位数），原地复用节点对象，
        并顺带回收墓碑。"""
        stack, nodes = [root], []
        while stack:
            n = stack.pop()
            nodes.append(n)
            if n.left:
                stack.append(n.left)
            if n.right:
                stack.append(n.right)
        live = [n for n in nodes if not n.dead]
        self._dead -= len(nodes) - len(live)
        if not live:
            return None

        def build(lst: List[_Node]) -> _Node:
            axis = self._spread_axis(lst)
            # 排序键加 pid 决胜，保证结构确定、可复现
            lst.sort(key=lambda n: (n.point[axis], n.pid))
            mid = len(lst) // 2
            node = lst[mid]
            node.axis = axis
            node.left = build(lst[:mid]) if mid > 0 else None
            node.right = build(lst[mid + 1:]) if mid + 1 < len(lst) else None
            node.size = len(lst)
            return node

        return build(live)

    def _spread_axis(self, nodes: List[_Node]) -> int:
        dim = self.dim
        mins = [math.inf] * dim
        maxs = [-math.inf] * dim
        for n in nodes:
            p = n.point
            for i in range(dim):
                v = p[i]
                if v < mins[i]:
                    mins[i] = v
                if v > maxs[i]:
                    maxs[i] = v
        best, best_spread = 0, -1.0
        for i in range(dim):
            spread = maxs[i] - mins[i]
            if spread > best_spread:
                best, best_spread = i, spread
        return best

    # ------------------------------------------------------------------ 查询

    def query(self, point: Sequence[float], k: int) -> List[Result]:
        """返回最近的至多 k 个点，按 (距离, pid) 升序。

        剪枝判据
        --------
        递归到节点时，设分割轴为 a，diff = q[a] - node.p[a]。
        先递归“近侧”子树；远侧子树中任一点与 q 的距离都 >= |diff|
        （仅该坐标轴上的差距就已这么大），因此当
            diff^2 > 当前第 k 好的距离平方
        时整个远侧子树不可能进入 Top-K，可安全剪掉。
        注意取严格大于：diff^2 等于当前第 k 距离时，远侧可能存在
        等距但 pid 更小的点，按决胜规则可能挤进结果，故不能剪。
        """
        q = self._check(point)
        if k < 0:
            raise ValueError("k must be >= 0")
        self.nodes_visited = 0
        if k == 0 or self._root is None:
            return []

        # 最小堆，堆顶是“当前 Top-K 中最差者”，键 (-dist_sq, -pid)
        heap: List[Tuple[float, int]] = []

        def recurse(node: Optional[_Node]) -> None:
            if node is None:
                return
            self.nodes_visited += 1
            if not node.dead:
                d = dist_sq(q, node.point)
                item = (-d, -node.pid)
                if len(heap) < k:
                    heapq.heappush(heap, item)
                elif item > heap[0]:
                    heapq.heapreplace(heap, item)
            axis = node.axis
            diff = q[axis] - node.point[axis]
            near = node.left if diff < 0 else node.right
            far = node.right if diff < 0 else node.left
            recurse(near)
            # 剪枝：堆未满必须搜；否则仅当超平面距离不超过第 k 距离时搜
            if len(heap) < k or diff * diff <= -heap[0][0]:
                recurse(far)

        recurse(self._root)
        out = sorted((-neg_d, -neg_pid) for neg_d, neg_pid in heap)
        return [(pid, self._nodes[pid].point, math.sqrt(d)) for d, pid in out]

    # ------------------------------------------------------------------ 工具

    def _check(self, point: Sequence[float]) -> Point:
        pt = tuple(float(c) for c in point)
        if len(pt) != self.dim:
            raise ValueError(f"expected {self.dim}-dim point, got {len(pt)}")
        return pt

    def items(self) -> List[Tuple[int, Point]]:
        """全部存活 (pid, point)，供对拍/调试。"""
        return [(n.pid, n.point) for n in self._nodes.values() if not n.dead]


def brute_force(points: Iterable[Tuple[int, Sequence[float]]],
                q: Sequence[float], k: int) -> List[Tuple[int, float]]:
    """暴力 Top-K：与 KDTree 使用同一排序键 (dist_sq, pid)，用于对拍。"""
    scored = [(dist_sq(q, p), pid) for pid, p in points]
    scored.sort()
    return [(pid, math.sqrt(d)) for d, pid in scored[:k]]
