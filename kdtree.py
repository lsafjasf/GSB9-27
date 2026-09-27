"""KD-tree: exact top-k nearest neighbor search with insert/delete.

Pure standard library. Points are tuples of floats of fixed dimension.

Ordering / tie-break rule
-------------------------
All results are ordered by the total order key ``(distance, point)`` where
``point`` compares lexicographically. This makes top-k deterministic even
when distances tie. The same key is used by the brute-force reference, so
results match exactly.

Pruning criterion
-----------------
Standard KD-tree branch-and-bound. At a node splitting on axis ``d`` with
value ``s``, the near subtree (the side containing the query) is visited
first. The far subtree is pruned iff the heap already holds ``k`` results
AND ``|query[d] - s| > worst_dist`` where ``worst_dist`` is the distance of
the current k-th best result. Rationale: every point in the far subtree is
at distance >= ``|query[d] - s|`` from the query, so if that lower bound
exceeds the current worst kept distance, no far point can enter the top-k.
Equality (``<=`` keeps the branch) is required for correctness under the
tie-break rule: a far point at exactly ``worst_dist`` may still win the
lexicographic tie-break.
"""

from __future__ import annotations

import heapq
import math
from typing import Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, ...]

__all__ = ["KDTree", "brute_force_topk", "euclidean"]


def euclidean(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


def brute_force_topk(points: Iterable[Point], query: Point, k: int) -> List[Tuple[float, Point]]:
    """Reference implementation: full scan, sorted by (distance, point)."""
    if k <= 0:
        return []
    scored = [(euclidean(query, p), p) for p in points]
    scored.sort(key=lambda t: (t[0], t[1]))
    return scored[:k]


class _Node:
    __slots__ = ("point", "left", "right")

    def __init__(self, point: Point):
        self.point = point
        self.left: Optional[_Node] = None
        self.right: Optional[_Node] = None


class KDTree:
    def __init__(self, dim: int):
        if dim < 1:
            raise ValueError("dim must be >= 1")
        self.dim = dim
        self.root: Optional[_Node] = None
        self.size = 0

    # ------------------------------------------------------------------ build
    @classmethod
    def build(cls, points: Iterable[Sequence[float]]) -> "KDTree":
        """Build a balanced tree from a point collection (median split)."""
        pts = [tuple(p) for p in points]
        if not pts:
            raise ValueError("need at least one point to infer dimension")
        tree = cls(len(pts[0]))
        for p in pts:
            if len(p) != tree.dim:
                raise ValueError("inconsistent point dimension")

        def rec(items: List[Point], depth: int) -> Optional[_Node]:
            if not items:
                return None
            axis = depth % tree.dim
            items.sort(key=lambda p: (p[axis], p))
            mid = len(items) // 2
            node = _Node(items[mid])
            node.left = rec(items[:mid], depth + 1)
            node.right = rec(items[mid + 1 :], depth + 1)
            return node

        tree.root = rec(pts, 0)
        tree.size = len(pts)
        return tree

    def __len__(self) -> int:
        return self.size

    def _check(self, point: Sequence[float]) -> Point:
        p = tuple(point)
        if len(p) != self.dim:
            raise ValueError(f"expected {self.dim}-d point, got {len(p)}-d")
        return p

    # ----------------------------------------------------------------- insert
    def insert(self, point: Sequence[float]) -> None:
        """Insert a point. Duplicates are stored as separate entries."""
        p = self._check(point)
        if self.root is None:
            self.root = _Node(p)
            self.size = 1
            return
        node = self.root
        depth = 0
        while True:
            axis = depth % self.dim
            if (p[axis], p) < (node.point[axis], node.point):
                if node.left is None:
                    node.left = _Node(p)
                    break
                node = node.left
            else:
                if node.right is None:
                    node.right = _Node(p)
                    break
                node = node.right
            depth += 1
        self.size += 1

    # ----------------------------------------------------------------- delete
    def delete(self, point: Sequence[float]) -> bool:
        """Delete one occurrence of ``point``. Returns True if removed."""
        p = self._check(point)
        self.root, removed = self._delete(self.root, p, 0)
        if removed:
            self.size -= 1
        return removed

    def _delete(self, node: Optional[_Node], p: Point, depth: int):
        if node is None:
            return None, False
        axis = depth % self.dim
        if p == node.point:
            if node.right is not None:
                m = self._find_min(node.right, axis, depth + 1)
                node.point = m.point
                node.right, _ = self._delete(node.right, m.point, depth + 1)
            elif node.left is not None:
                m = self._find_min(node.left, axis, depth + 1)
                node.point = m.point
                # Remaining left-subtree points are all >= m.point on `axis`,
                # so the subtree becomes the new right child.
                node.right, _ = self._delete(node.left, m.point, depth + 1)
                node.left = None
            else:
                return None, True
            return node, True
        if (p[axis], p) < (node.point[axis], node.point):
            node.left, removed = self._delete(node.left, p, depth + 1)
        else:
            node.right, removed = self._delete(node.right, p, depth + 1)
        return node, removed

    def _find_min(self, node: _Node, axis: int, depth: int) -> _Node:
        """Min node by key (point[axis], point) in the subtree."""
        best = node
        cur_axis = depth % self.dim
        if node.left is not None:
            cand = self._find_min(node.left, axis, depth + 1)
            if (cand.point[axis], cand.point) < (best.point[axis], best.point):
                best = cand
        # If the current split axis == target axis, the right subtree cannot
        # contain a smaller key (all its points route >= node on that axis).
        if cur_axis != axis and node.right is not None:
            cand = self._find_min(node.right, axis, depth + 1)
            if (cand.point[axis], cand.point) < (best.point[axis], best.point):
                best = cand
        return best

    # ------------------------------------------------------------------ query
    def query(self, query: Sequence[float], k: int) -> List[Tuple[float, Point]]:
        """Top-k nearest neighbors, sorted by (distance, point)."""
        results, _ = self.query_with_stats(query, k)
        return results

    def query_with_stats(self, query: Sequence[float], k: int):
        """Like query(), also returning the number of visited nodes."""
        q = self._check(query)
        if k <= 0 or self.root is None:
            return [], 0

        heap: List[Tuple[float, Tuple[float, ...]]] = []  # max-heap via negation
        visited = 0
        dim_count = self.dim

        def rec(node: Optional[_Node], depth: int) -> None:
            nonlocal visited
            if node is None:
                return
            axis = depth % dim_count
            diff = q[axis] - node.point[axis]
            near, far = (node.left, node.right) if diff < 0 else (node.right, node.left)

            rec(near, depth + 1)

            visited += 1
            dist = euclidean(q, node.point)
            entry = (-dist, tuple(-c for c in node.point))
            if len(heap) < k:
                heapq.heappush(heap, entry)
            elif entry > heap[0]:
                heapq.heapreplace(heap, entry)

            # Pruning: skip the far branch only when the splitting plane is
            # strictly farther than the current k-th best distance.
            if len(heap) < k or abs(diff) <= -heap[0][0]:
                rec(far, depth + 1)

        rec(self.root, 0)
        results = sorted(
            ((-neg_dist, tuple(-c for c in neg_point)) for neg_dist, neg_point in heap),
            key=lambda t: (t[0], t[1]),
        )
        return results, visited
