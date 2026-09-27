"""Invariant and behaviour tests for tree_move.  Run: python3 -m unittest test_tree_move -v"""

import itertools
import sys
import unittest

from tree_move import BatchMoveError, Forest, MoveError


def snapshot(forest):
    """Order-sensitive structural snapshot of the whole forest."""
    return {
        nid: (n.parent.id if n.parent else None,
              tuple(c.id for c in n.children),
              n.depth)
        for nid, n in forest.nodes.items()
    }, tuple(forest.roots)


def sample_forest():
    f = Forest()
    # tree A:  r1 +- a +- b +- c
    #          |   +- d
    # tree B:  r2 +- e +- f
    f.add_root("r1")
    f.add_child("r1", "a")
    f.add_child("a", "b")
    f.add_child("b", "c")
    f.add_child("r1", "d")
    f.add_root("r2")
    f.add_child("r2", "e")
    f.add_child("e", "f")
    return f


def build_chain(forest, prefix, n):
    forest.add_root(prefix + "0")
    for i in range(1, n):
        forest.add_child(prefix + str(i - 1), prefix + str(i))


class TestSingleMove(unittest.TestCase):
    def test_move_basic_updates_structure_and_depths(self):
        f = sample_forest()
        f.move("b", "d")
        self.assertEqual(f.parent_id("b"), "d")
        self.assertEqual(f.nodes["b"].depth, 2)
        self.assertEqual(f.nodes["c"].depth, 3)
        self.assertEqual(f.path("c"), ["r1", "d", "b", "c"])
        self.assertEqual([c.id for c in f.nodes["a"].children], [])
        f.check_invariants()

    def test_move_to_self_rejected_with_path(self):
        f = sample_forest()
        before = snapshot(f)
        with self.assertRaises(MoveError) as ctx:
            f.move("b", "b")
        self.assertEqual(ctx.exception.path, ["b"])
        self.assertEqual(snapshot(f), before)
        f.check_invariants()

    def test_move_to_direct_child_rejected_with_path(self):
        f = sample_forest()
        before = snapshot(f)
        with self.assertRaises(MoveError) as ctx:
            f.move("a", "b")
        self.assertEqual(ctx.exception.path, ["a", "b"])
        self.assertEqual(snapshot(f), before)
        f.check_invariants()

    def test_move_to_deep_descendant_rejected_with_full_path(self):
        f = sample_forest()
        with self.assertRaises(MoveError) as ctx:
            f.move("a", "c")
        self.assertEqual(ctx.exception.path, ["a", "b", "c"])
        f.check_invariants()

    def test_move_root_under_own_descendant_rejected(self):
        f = sample_forest()
        before = snapshot(f)
        with self.assertRaises(MoveError) as ctx:
            f.move("r1", "c")
        self.assertEqual(ctx.exception.path, ["r1", "a", "b", "c"])
        self.assertEqual(snapshot(f), before)
        f.check_invariants()

    def test_move_root_under_other_tree(self):
        f = sample_forest()
        f.move("r1", "f")
        self.assertEqual(f.roots, ["r2"])
        self.assertEqual(f.nodes["r1"].depth, 3)
        self.assertEqual(f.nodes["a"].depth, 4)
        self.assertEqual(f.nodes["c"].depth, 6)
        self.assertEqual(f.nodes["d"].depth, 4)
        self.assertEqual(f.path("c"), ["r2", "e", "f", "r1", "a", "b", "c"])
        f.check_invariants()

    def test_detach_subtree_to_new_root(self):
        f = sample_forest()
        f.move("b", None)
        self.assertIn("b", f.roots)
        self.assertEqual(f.nodes["b"].depth, 0)
        self.assertEqual(f.nodes["c"].depth, 1)
        self.assertEqual(f.path("c"), ["b", "c"])
        f.check_invariants()

    def test_move_noop_same_parent(self):
        f = sample_forest()
        before = snapshot(f)
        f.move("b", "a")          # already its parent
        f.move("r1", None)        # already a root
        self.assertEqual(snapshot(f), before)
        f.check_invariants()

    def test_unknown_ids(self):
        f = sample_forest()
        with self.assertRaises(KeyError):
            f.move("nope", "a")
        with self.assertRaises(KeyError):
            f.move("a", "nope")

    def test_path_length_matches_depth_field(self):
        f = sample_forest()
        f.move("r1", "f")
        f.move("d", "e")
        for nid, n in f.nodes.items():
            self.assertEqual(len(f.path(nid)) - 1, n.depth, nid)
        f.check_invariants()


class TestBatchMove(unittest.TestCase):
    MOVES = [("a", "r2"), ("b", "d"), ("f", "a")]

    def test_batch_success(self):
        f = sample_forest()
        f.apply_batch(self.MOVES)
        self.assertEqual(f.parent_id("a"), "r2")
        self.assertEqual(f.parent_id("b"), "d")
        self.assertEqual(f.parent_id("f"), "a")
        self.assertEqual(f.path("c"), ["r1", "d", "b", "c"])
        self.assertEqual(f.path("f"), ["r2", "a", "f"])
        f.check_invariants()
        for nid, n in f.nodes.items():
            self.assertEqual(len(f.path(nid)) - 1, n.depth, nid)

    def test_batch_order_independence(self):
        reference = None
        for perm in itertools.permutations(self.MOVES):
            f = sample_forest()
            f.apply_batch(perm)
            snap = snapshot(f)
            if reference is None:
                reference = snap
            self.assertEqual(snap, reference)
            f.check_invariants()

    def test_batch_composed_cycle_rejected_atomically(self):
        f = sample_forest()
        before = snapshot(f)
        # each move is individually valid, but together they close a cycle:
        # r1 -> e (in tree B) and r2 -> a (in tree A)
        with self.assertRaises(BatchMoveError) as ctx:
            f.apply_batch([("r1", "e"), ("r2", "a")])
        cycle = ctx.exception.cycle_path
        self.assertIsNotNone(cycle)
        self.assertEqual(cycle[0], cycle[-1])
        self.assertEqual(set(cycle[:-1]), {"r1", "a", "r2", "e"})
        self.assertEqual(snapshot(f), before)   # nothing applied
        f.check_invariants()

    def test_batch_individual_conflict_rejected_atomically(self):
        f = sample_forest()
        before = snapshot(f)
        with self.assertRaises(BatchMoveError) as ctx:
            f.apply_batch([("a", "c"), ("d", "r2")])   # first move conflicts
        self.assertEqual(len(ctx.exception.errors), 1)
        self.assertEqual(ctx.exception.errors[0].path, ["a", "b", "c"])
        self.assertEqual(snapshot(f), before)
        f.check_invariants()

    def test_batch_duplicate_source_rejected(self):
        f = sample_forest()
        before = snapshot(f)
        with self.assertRaises(BatchMoveError):
            f.apply_batch([("a", "r2"), ("a", "d")])
        self.assertEqual(snapshot(f), before)

    def test_batch_unknown_ids_rejected(self):
        f = sample_forest()
        before = snapshot(f)
        with self.assertRaises(BatchMoveError) as ctx:
            f.apply_batch([("ghost", "a"), ("b", "ghost2")])
        self.assertEqual(len(ctx.exception.errors), 2)
        self.assertEqual(snapshot(f), before)

    def test_batch_detach_and_reattach_mix(self):
        f = sample_forest()
        f.apply_batch([("b", None), ("e", "c"), ("d", "f")])
        self.assertIn("b", f.roots)
        self.assertEqual(f.path("f"), ["b", "c", "e", "f"])
        self.assertEqual(f.path("d"), ["b", "c", "e", "f", "d"])
        f.check_invariants()
        for nid, n in f.nodes.items():
            self.assertEqual(len(f.path(nid)) - 1, n.depth, nid)


class TestDeepTree(unittest.TestCase):
    N = 50_000  # far beyond the default recursion limit of 1000

    def test_deep_chain_moves_no_recursion(self):
        self.assertLessEqual(sys.getrecursionlimit(), 1000)
        f = Forest()
        build_chain(f, "c", self.N)
        f.check_invariants()

        # move the middle of the chain under the root: 25k-node subtree
        f.move("c%d" % (self.N // 2), "c0")
        f.check_invariants()
        deepest = "c%d" % (self.N - 1)
        self.assertEqual(f.nodes[deepest].depth, self.N // 2)
        self.assertEqual(len(f.path(deepest)) - 1, self.N // 2)

        # move it back
        f.move("c%d" % (self.N // 2), "c%d" % (self.N // 2 - 1))
        f.check_invariants()
        self.assertEqual(f.nodes[deepest].depth, self.N - 1)

    def test_deep_chain_conflict_path(self):
        f = Forest()
        build_chain(f, "c", self.N)
        with self.assertRaises(MoveError) as ctx:
            f.move("c10", "c%d" % (self.N - 1))
        path = ctx.exception.path
        self.assertEqual(path[0], "c10")
        self.assertEqual(path[-1], "c%d" % (self.N - 1))
        self.assertEqual(len(path), self.N - 10)
        f.check_invariants()

    def test_root_move_makes_100k_deep_tree(self):
        f = Forest()
        build_chain(f, "a", self.N)
        build_chain(f, "b", self.N)
        # root of chain B under the deepest node of chain A (cross-tree root move)
        f.move("b0", "a%d" % (self.N - 1))
        f.check_invariants()
        deepest = "b%d" % (self.N - 1)
        self.assertEqual(f.nodes[deepest].depth, 2 * self.N - 1)
        self.assertEqual(len(f.path(deepest)) - 1, 2 * self.N - 1)

    def test_deep_batch(self):
        f = Forest()
        build_chain(f, "c", self.N)
        # move every 100th node under the root: 500 moves, one batch
        moves = [("c%d" % i, "c0") for i in range(100, self.N, 100)]
        f.apply_batch(moves)
        f.check_invariants()
        for nid, n in f.nodes.items():
            self.assertEqual(len(f.path(nid)) - 1, n.depth, nid)


class TestInvariantChecker(unittest.TestCase):
    def test_checker_catches_corruption(self):
        f = sample_forest()
        f.nodes["b"].depth = 42
        with self.assertRaises(AssertionError):
            f.check_invariants()

    def test_checker_catches_cycle(self):
        f = sample_forest()
        # hand-craft a cycle a -> b -> a, bypassing the guards
        a, b = f.nodes["a"], f.nodes["b"]
        a.parent = b
        b.children.append(a)
        f.roots.remove("r1")
        f.roots.append("r1")  # r1 still listed; a detached cycle remains
        with self.assertRaises(AssertionError):
            f.check_invariants()


if __name__ == "__main__":
    unittest.main()
