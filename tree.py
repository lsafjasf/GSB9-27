"""Iterative tree/forest move library (Python 3, stdlib only).

Design notes:
- No recursion anywhere: cycle detection, depth fix-up and validation all
  use explicit stacks/loops, so arbitrarily deep trees are safe.
- Every node carries a `depth` field maintained on move; `path_to()` walks
  parent pointers, so it never depends on the depth field being correct.
- Batch moves are settled against a canonical (sorted) order, so the result
  of a batch never depends on the input order of the moves.
"""

from __future__ import annotations


class CycleError(Exception):
    """Raised when a move would attach a node under itself or a descendant."""

    def __init__(self, conflict_path):
        self.conflict_path = list(conflict_path)
        super().__init__(
            "move rejected: target is the node itself or its descendant; "
            "conflict path: " + " -> ".join(map(str, self.conflict_path))
        )


class Node:
    __slots__ = ("id", "parent", "children", "depth")

    def __init__(self, node_id):
        self.id = node_id
        self.parent = None
        self.children = []
        self.depth = 0

    def __repr__(self):
        return f"Node({self.id!r}, depth={self.depth})"


class MoveResult:
    __slots__ = ("node_id", "target_id", "ok", "conflict_path")

    def __init__(self, node_id, target_id, ok, conflict_path=None):
        self.node_id = node_id
        self.target_id = target_id
        self.ok = ok
        self.conflict_path = conflict_path

    def __repr__(self):
        if self.ok:
            return f"MoveResult({self.node_id!r} -> {self.target_id!r}: ok)"
        return (f"MoveResult({self.node_id!r} -> {self.target_id!r}: "
                f"rejected, conflict={self.conflict_path!r})")


class Forest:
    """A forest of nodes; a single tree is a forest with one root."""

    def __init__(self):
        self.nodes = {}
        self.roots = []

    def add_node(self, node_id, parent_id=None):
        if node_id in self.nodes:
            raise ValueError(f"duplicate node id: {node_id!r}")
        node = Node(node_id)
        self.nodes[node_id] = node
        if parent_id is None:
            self.roots.append(node)
        else:
            parent = self.nodes[parent_id]
            node.parent = parent
            node.depth = parent.depth + 1
            parent.children.append(node)
        return node

    def path_to(self, node_id):
        """Return the id path from the root down to `node_id` (iterative)."""
        node = self.nodes[node_id]
        path = []
        while node is not None:
            path.append(node.id)
            node = node.parent
        path.reverse()
        return path

    def conflict_path(self, node_id, target_id):
        """If moving `node_id` under `target_id` would create a cycle, return
        the path from `node_id` down to `target_id`; otherwise return None."""
        node = self.nodes[node_id]
        target = self.nodes[target_id]
        path = []
        cur = target
        while cur is not None:
            path.append(cur.id)
            if cur is node:
                path.reverse()
                return path
            cur = cur.parent
        return None

    def can_move(self, node_id, target_id):
        return target_id is None or self.conflict_path(node_id, target_id) is None

    def move(self, node_id, target_id):
        """Attach `node_id` (with its subtree) under `target_id`.

        `target_id=None` detaches the node into a new root.
        Raises CycleError (carrying the conflict path) on cyclic moves.
        """
        node = self.nodes[node_id]
        if target_id is not None:
            conflict = self.conflict_path(node_id, target_id)
            if conflict is not None:
                raise CycleError(conflict)
            target = self.nodes[target_id]
        else:
            target = None

        if node.parent is target:
            return  # no-op, already in place

        if node.parent is None:
            self.roots.remove(node)
        else:
            node.parent.children.remove(node)

        node.parent = target
        if target is None:
            self.roots.append(node)
        else:
            target.children.append(node)

        self._fix_depths(node)

    @staticmethod
    def _fix_depths(node):
        """Recompute depth for the moved subtree, iteratively."""
        base = node.parent.depth + 1 if node.parent is not None else 0
        stack = [(node, base)]
        while stack:
            cur, depth = stack.pop()
            cur.depth = depth
            for child in cur.children:
                stack.append((child, depth + 1))

    def move_batch(self, moves):
        """Settle a batch of (node_id, target_id) moves.

        Moves are applied in a canonical sorted order, so any permutation of
        the same batch yields exactly the same final tree and the same
        per-move outcomes. Valid moves are applied; cyclic ones are reported
        with their conflict path. Returns a list of MoveResult.
        """
        indexed = list(enumerate(moves))
        indexed.sort(key=lambda pair: (pair[1][0], pair[1][1] is None,
                                       pair[1][1] if pair[1][1] is not None
                                       else pair[1][0]))
        results = [None] * len(indexed)
        for original_index, (node_id, target_id) in indexed:
            try:
                self.move(node_id, target_id)
            except CycleError as err:
                results[original_index] = MoveResult(
                    node_id, target_id, False, err.conflict_path)
            else:
                results[original_index] = MoveResult(node_id, target_id, True)
        return results

    def validate(self):
        """Assert all tree invariants iteratively. Returns (count, max_depth).

        Invariants checked:
        - every node is reachable from the roots exactly once (no cycles,
          no shared children, no orphans);
        - parent/children links are mutually consistent (single parent);
        - each node's depth field equals its actual path length minus one.
        """
        for root in self.roots:
            assert root.parent is None, f"root {root.id!r} has a parent"

        seen = set()
        max_depth = 0
        stack = [(root, 0) for root in self.roots]
        while stack:
            node, depth = stack.pop()
            assert node.id not in seen, (
                f"node {node.id!r} reachable twice (cycle or two parents)")
            seen.add(node.id)
            assert node.depth == depth, (
                f"node {node.id!r}: depth field {node.depth} != actual {depth}")
            if depth > max_depth:
                max_depth = depth
            for child in node.children:
                assert child.parent is node, (
                    f"child {child.id!r} does not point back to {node.id!r}")
                stack.append((child, depth + 1))

        for node in self.nodes.values():
            if node.parent is None:
                assert node in self.roots, (
                    f"parentless node {node.id!r} missing from roots")
            else:
                assert node in node.parent.children, (
                    f"node {node.id!r} missing from parent's children")

        assert len(seen) == len(self.nodes), (
            f"{len(self.nodes) - len(seen)} node(s) unreachable from roots")
        return len(seen), max_depth

    def parent_map(self):
        """Snapshot of the structure, handy for equality checks."""
        return {node_id: (node.parent.id if node.parent else None)
                for node_id, node in self.nodes.items()}


def build_chain(forest, n, prefix="c"):
    """Build a chain prefix0 -> prefix1 -> ... -> prefix(n-1)."""
    parent = None
    for i in range(n):
        forest.add_node(f"{prefix}{i}", parent)
        parent = f"{prefix}{i}"


if __name__ == "__main__":
    f = Forest()
    #      a                a
    #     / \      =>        b
    #    b   c              / \
    #   / \                d   c
    #  d   e                  (e moved under c)
    for nid, pid in [("a", None), ("b", "a"), ("c", "a"),
                     ("d", "b"), ("e", "b")]:
        f.add_node(nid, pid)
    f.move("e", "c")
    print("path to e:", " -> ".join(f.path_to("e")))
    try:
        f.move("a", "d")
    except CycleError as err:
        print("rejected:", err)
    print("invariants (nodes, max_depth):", f.validate())
