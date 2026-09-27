"""Cycle-safe movable forest (multiple rooted trees) with atomic batch moves.

Design notes
------------
* Every traversal (path query, depth fix-up, cycle detection, invariant
  checking) is iterative, so trees far deeper than Python's recursion
  limit are supported.
* A move is rejected *before* any mutation when the target is the node
  itself or one of its descendants; the error carries the conflict path
  (ancestor -> ... -> target) that would have closed the cycle.
* Batch moves are settled atomically against the pre-batch state: every
  move is validated, the composed parent map is checked for cycles, and
  only then is the batch applied in a canonical order.  The result
  therefore depends only on the *set* of moves, never on their order.

Standard library only.
"""

from __future__ import annotations


class MoveError(Exception):
    """A single move was rejected before any mutation happened.

    ``path`` is the conflict path (list of node ids, ancestor first)
    showing why the move would close a cycle; ``None`` for other reasons.
    """

    def __init__(self, reason, path=None):
        super().__init__(reason)
        self.reason = reason
        self.path = path


class BatchMoveError(Exception):
    """A batch was rejected; nothing was applied (atomic settlement).

    ``errors`` holds one MoveError per invalid move; ``cycle_path`` is the
    cycle (first id repeated at the end) found in the composed parent map.
    """

    def __init__(self, errors=(), cycle_path=None):
        self.errors = list(errors)
        self.cycle_path = cycle_path
        parts = [e.reason for e in self.errors]
        if cycle_path:
            parts.append("composed cycle: " + " -> ".join(map(str, cycle_path)))
        super().__init__("; ".join(parts) or "batch rejected")


class Node:
    __slots__ = ("id", "parent", "children", "depth")

    def __init__(self, node_id):
        self.id = node_id
        self.parent = None      # Node | None
        self.children = []      # list[Node]
        self.depth = 0          # edges from the root of its tree

    def __repr__(self):
        return "Node(%r, depth=%d)" % (self.id, self.depth)


class Forest:
    """A forest of rooted trees supporting cycle-safe subtree moves."""

    def __init__(self):
        self.nodes = {}   # id -> Node
        self.roots = []   # root ids, insertion order

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _get(self, node_id):
        try:
            return self.nodes[node_id]
        except KeyError:
            raise KeyError("unknown node: %r" % (node_id,)) from None

    def add_root(self, node_id):
        if node_id in self.nodes:
            raise ValueError("duplicate node: %r" % (node_id,))
        node = Node(node_id)
        self.nodes[node_id] = node
        self.roots.append(node_id)
        return node

    def add_child(self, parent_id, node_id):
        parent = self._get(parent_id)
        if node_id in self.nodes:
            raise ValueError("duplicate node: %r" % (node_id,))
        node = Node(node_id)
        node.parent = parent
        node.depth = parent.depth + 1
        parent.children.append(node)
        self.nodes[node_id] = node
        return node

    # ------------------------------------------------------------------
    # queries (all iterative)
    # ------------------------------------------------------------------
    def parent_id(self, node_id):
        node = self._get(node_id)
        return node.parent.id if node.parent is not None else None

    def path(self, node_id):
        """Ids on the path root -> ... -> node.  O(depth), no recursion."""
        node = self._get(node_id)
        out = []
        while node is not None:
            out.append(node.id)
            node = node.parent
        out.reverse()
        return out

    def is_ancestor(self, ancestor_id, node_id):
        node = self._get(node_id)
        while node is not None:
            if node.id == ancestor_id:
                return True
            node = node.parent
        return False

    def subtree_ids(self, node_id):
        out = []
        stack = [self._get(node_id)]
        while stack:
            node = stack.pop()
            out.append(node.id)
            stack.extend(node.children)
        return out

    def conflict_path(self, node_id, target_id):
        """Path node -> ... -> target if moving ``node_id`` under
        ``target_id`` would close a cycle, else ``None``.  Iterative."""
        self._get(node_id)
        node = self._get(target_id)
        chain = []
        while node is not None:
            chain.append(node.id)
            if node.id == node_id:
                chain.reverse()
                return chain
            node = node.parent
        return None

    # ------------------------------------------------------------------
    # single move
    # ------------------------------------------------------------------
    def move(self, node_id, new_parent_id):
        """Move the subtree rooted at ``node_id`` under ``new_parent_id``,
        or make it a new root when ``new_parent_id`` is None.

        Raises MoveError (carrying the conflict path) if the move would
        close a cycle; nothing is mutated in that case.
        """
        node = self._get(node_id)
        if new_parent_id is None:
            if node.parent is None:
                return  # already a root: no-op
            self._detach(node)
            self.roots.append(node.id)
            self._fix_depths(node, 0)
            return
        target = self._get(new_parent_id)
        conflict = self.conflict_path(node_id, new_parent_id)
        if conflict is not None:
            raise MoveError(
                "cannot move %r under %r: target is the node itself or its "
                "descendant (cycle)" % (node_id, new_parent_id),
                path=conflict,
            )
        if node.parent is target:
            return  # no-op
        self._detach(node)
        target.children.append(node)
        node.parent = target
        self._fix_depths(node, target.depth + 1)

    # ------------------------------------------------------------------
    # batch move (atomic settlement, order independent)
    # ------------------------------------------------------------------
    def apply_batch(self, moves):
        """Settle a batch of moves atomically.

        ``moves`` is an iterable of ``(node_id, new_parent_id)`` pairs.
        Every move is validated against the pre-batch state and the
        composed parent map is checked for cycles; if anything is invalid
        a BatchMoveError is raised and nothing is applied.  The outcome
        depends only on the set of moves, never on their order.
        """
        overrides = {}
        errors = []
        for node_id, target_id in moves:
            if node_id in overrides:
                errors.append(MoveError(
                    "node %r is moved twice in one batch" % (node_id,)))
                continue
            if node_id not in self.nodes:
                errors.append(MoveError("unknown node: %r" % (node_id,)))
                continue
            overrides[node_id] = target_id
            if target_id is None:
                continue
            if target_id not in self.nodes:
                errors.append(MoveError("unknown target: %r" % (target_id,)))
                continue
            conflict = self.conflict_path(node_id, target_id)
            if conflict is not None:
                errors.append(MoveError(
                    "cannot move %r under %r: target is the node itself or "
                    "its descendant (cycle)" % (node_id, target_id),
                    path=conflict,
                ))
        if errors:
            raise BatchMoveError(errors)
        cycle = self._composed_cycle(overrides)
        if cycle is not None:
            raise BatchMoveError(cycle_path=cycle)
        self._apply(overrides)

    def _composed_parent(self, node_id, overrides):
        if node_id in overrides:
            return overrides[node_id]
        parent = self.nodes[node_id].parent
        return parent.id if parent is not None else None

    def _composed_cycle(self, overrides):
        """Return a cycle (list of ids, first repeated at the end) in the
        would-be parent map, or None.  Iterative."""
        safe = set()
        for start in overrides:
            if start in safe:
                continue
            chain = []
            pos = {}
            cur = start
            while cur is not None and cur not in pos and cur not in safe:
                pos[cur] = len(chain)
                chain.append(cur)
                cur = self._composed_parent(cur, overrides)
            if cur is not None and cur in pos:
                return chain[pos[cur]:] + [cur]
            safe.update(chain)
        return None

    def _apply(self, overrides):
        # Final depth of every node on a moved node's ancestor chain,
        # computed iteratively with memoisation over the composed map.
        memo = {}
        for start in overrides:
            stack = []
            cur = start
            while cur is not None and cur not in memo:
                stack.append(cur)
                cur = self._composed_parent(cur, overrides)
            depth = memo[cur] if cur is not None else -1
            while stack:
                depth += 1
                memo[stack.pop()] = depth
        # Canonical attach order: parents before children, ties broken by
        # repr(id), so the result is independent of the input move order.
        order = sorted(overrides, key=lambda nid: (memo[nid], repr(nid)))
        for nid in order:
            self._detach(self.nodes[nid])
        for nid in order:
            node = self.nodes[nid]
            target_id = overrides[nid]
            if target_id is None:
                self.roots.append(node.id)
                self._fix_depths(node, 0)
            else:
                target = self.nodes[target_id]
                target.children.append(node)
                node.parent = target
                self._fix_depths(node, target.depth + 1)

    # ------------------------------------------------------------------
    # invariants
    # ------------------------------------------------------------------
    def check_invariants(self):
        """Assert structural invariants; raises AssertionError.  Iterative.

        * every node is reachable from exactly one root (no cycles, no
          orphaned cycles, no node with two parents)
        * parent/child pointers are mutually consistent
        * depth fields match the real path length from the root
        """
        root_set = set(self.roots)
        assert len(root_set) == len(self.roots), "duplicate roots"
        seen = set()
        for root_id in self.roots:
            root = self.nodes[root_id]
            assert root.parent is None, "root %r has a parent" % (root_id,)
            assert root.depth == 0, "root %r has depth %d" % (root_id, root.depth)
            stack = [root]
            while stack:
                node = stack.pop()
                assert node.id not in seen, (
                    "node %r reachable twice (cycle or multiple parents)"
                    % (node.id,))
                seen.add(node.id)
                for child in node.children:
                    assert child.parent is node, (
                        "child %r does not point back to parent %r"
                        % (child.id, node.id))
                    assert child.depth == node.depth + 1, (
                        "node %r has depth %d, expected %d"
                        % (child.id, child.depth, node.depth + 1))
                stack.extend(node.children)
        assert len(seen) == len(self.nodes), (
            "%d nodes not reachable from any root (cycle or orphan)"
            % (len(self.nodes) - len(seen),))
        # NB: membership of each node in its parent's children and the
        # child->parent back-reference are already verified by the root
        # traversal above (every reachable node is visited exactly once,
        # via its parent's children list, and must point back).
        for node in self.nodes.values():
            if node.parent is None:
                assert node.id in root_set, "root %r missing from roots" % (node.id,)
            else:
                assert node.id not in root_set, "non-root %r listed as root" % (node.id,)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _detach(self, node):
        if node.parent is None:
            self.roots.remove(node.id)
        else:
            node.parent.children.remove(node)
            node.parent = None

    @staticmethod
    def _fix_depths(node, new_depth):
        delta = new_depth - node.depth
        if delta == 0:
            return
        stack = [node]
        while stack:
            cur = stack.pop()
            cur.depth += delta
            stack.extend(cur.children)
