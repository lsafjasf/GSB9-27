"""Invariant and performance tests for tree.py (stdlib unittest only)."""

import random
import time
import unittest

from tree import CycleError, Forest, build_chain


def make_sample_tree():
    """Sample tree: a(b(d,e), c(f(g,h)))."""
    f = Forest()
    for nid, pid in [("a", None), ("b", "a"), ("c", "a"), ("d", "b"),
                     ("e", "b"), ("f", "c"), ("g", "f"), ("h", "f")]:
        f.add_node(nid, pid)
    return f


class TestCycleDetection(unittest.TestCase):
    def test_move_to_self(self):
        f = make_sample_tree()
        with self.assertRaises(CycleError) as ctx:
            f.move("b", "b")
        self.assertEqual(ctx.exception.conflict_path, ["b"])
        f.validate()

    def test_move_to_direct_child(self):
        f = make_sample_tree()
        with self.assertRaises(CycleError) as ctx:
            f.move("b", "d")
        self.assertEqual(ctx.exception.conflict_path, ["b", "d"])
        # rejected move must leave the tree untouched
        self.assertEqual(f.path_to("d"), ["a", "b", "d"])
        f.validate()

    def test_move_to_deep_descendant_conflict_path(self):
        f = make_sample_tree()
        with self.assertRaises(CycleError) as ctx:
            f.move("a", "g")
        self.assertEqual(ctx.exception.conflict_path, ["a", "c", "f", "g"])
        f.validate()

    def test_conflict_path_query(self):
        f = make_sample_tree()
        self.assertEqual(f.conflict_path("c", "h"), ["c", "f", "h"])
        self.assertIsNone(f.conflict_path("c", "d"))
        self.assertTrue(f.can_move("c", "d"))
        self.assertFalse(f.can_move("a", "a"))


class TestMoves(unittest.TestCase):
    def test_normal_move_updates_depths_and_paths(self):
        f = make_sample_tree()
        f.move("f", "b")  # f (with g,h) under b
        self.assertEqual(f.path_to("g"), ["a", "b", "f", "g"])
        self.assertEqual(f.nodes["f"].depth, 2)
        self.assertEqual(f.nodes["g"].depth, 3)
        self.assertEqual(f.nodes["h"].depth, 3)
        self.assertEqual(len(f.nodes["c"].children), 0)
        f.validate()

    def test_move_to_same_parent_is_noop(self):
        f = make_sample_tree()
        before = f.parent_map()
        f.move("d", "b")
        self.assertEqual(f.parent_map(), before)
        f.validate()

    def test_root_move_under_other_tree(self):
        f = Forest()
        build_chain(f, 3, "x")   # x0 -> x1 -> x2
        build_chain(f, 2, "y")   # y0 -> y1
        self.assertEqual(len(f.roots), 2)
        f.move("x0", "y1")       # whole x-chain under y1
        self.assertEqual(len(f.roots), 1)
        self.assertEqual(f.path_to("x2"), ["y0", "y1", "x0", "x1", "x2"])
        self.assertEqual(f.nodes["x2"].depth, 4)
        f.validate()

    def test_root_move_under_own_descendant_rejected(self):
        f = Forest()
        build_chain(f, 5, "r")
        with self.assertRaises(CycleError) as ctx:
            f.move("r0", "r4")
        self.assertEqual(ctx.exception.conflict_path,
                         ["r0", "r1", "r2", "r3", "r4"])
        self.assertEqual(len(f.roots), 1)
        f.validate()

    def test_detach_to_new_root(self):
        f = make_sample_tree()
        f.move("f", None)
        self.assertEqual(f.path_to("g"), ["f", "g"])
        self.assertEqual(f.nodes["f"].depth, 0)
        self.assertEqual(f.nodes["g"].depth, 1)
        self.assertEqual(len(f.roots), 2)
        f.validate()


class TestBatch(unittest.TestCase):
    def test_batch_reports_conflicts_and_applies_valid(self):
        f = make_sample_tree()
        results = f.move_batch([("e", "f"),   # ok
                                ("a", "h"),   # cycle: a -> c -> f -> h
                                ("d", "g")])  # ok
        self.assertTrue(results[0].ok)
        self.assertFalse(results[1].ok)
        self.assertEqual(results[1].conflict_path, ["a", "c", "f", "h"])
        self.assertTrue(results[2].ok)
        self.assertEqual(f.path_to("e"), ["a", "c", "f", "e"])
        self.assertEqual(f.path_to("d"), ["a", "c", "f", "g", "d"])
        f.validate()

    def test_batch_order_independence(self):
        moves = [("e", "f"), ("d", "g"), ("f", "e"), ("a", "h"),
                 ("b", "c"), ("h", "d"), ("g", None)]
        snapshots = []
        outcomes = []
        for seed in range(8):
            f = make_sample_tree()
            shuffled = moves[:]
            random.Random(seed).shuffle(shuffled)
            results = f.move_batch(shuffled)
            f.validate()
            snapshots.append(f.parent_map())
            # per-move outcome keyed by the move itself, not position
            outcomes.append(sorted(
                (r.node_id, r.target_id, r.ok,
                 tuple(r.conflict_path or ())) for r in results))
        for snap in snapshots[1:]:
            self.assertEqual(snapshots[0], snap)
        for outcome in outcomes[1:]:
            self.assertEqual(outcomes[0], outcome)

    def test_batch_mutual_cycle_is_deterministic(self):
        # a->b->c chain; swapping b and c is a mutual cycle: exactly one of
        # the two moves must win, and it must be the same one every time.
        finals = []
        for seed in range(6):
            f = Forest()
            build_chain(f, 4, "n")  # n0->n1->n2->n3
            moves = [("n1", "n3"), ("n2", "n1")]
            random.Random(seed).shuffle(moves)
            f.move_batch(moves)
            f.validate()
            finals.append(f.parent_map())
        for snap in finals[1:]:
            self.assertEqual(finals[0], snap)


class TestDeepTree(unittest.TestCase):
    DEPTH = 50_000

    def test_deep_tree_move_and_validate(self):
        f = Forest()
        t0 = time.perf_counter()
        build_chain(f, self.DEPTH, "n")
        t1 = time.perf_counter()

        # move the middle of the chain under the root: subtree of 25k nodes
        mid = f"n{self.DEPTH // 2}"
        f.move(mid, "n0")
        t2 = time.perf_counter()

        count, max_depth = f.validate()
        t3 = time.perf_counter()

        self.assertEqual(count, self.DEPTH)
        self.assertEqual(f.nodes[mid].depth, 1)
        self.assertEqual(f.nodes[f"n{self.DEPTH - 1}"].depth,
                         1 + (self.DEPTH - 1 - self.DEPTH // 2))

        # cycle detection on the deep tree must not blow the stack
        with self.assertRaises(CycleError) as ctx:
            f.move("n0", f"n{self.DEPTH - 1}")
        self.assertEqual(len(ctx.exception.conflict_path),
                         2 + self.DEPTH - 1 - self.DEPTH // 2)
        t4 = time.perf_counter()

        print(f"\n[perf] build {self.DEPTH}-node chain : {(t1 - t0) * 1e3:8.2f} ms")
        print(f"[perf] move 25k-node subtree           : {(t2 - t1) * 1e3:8.2f} ms")
        print(f"[perf] validate {self.DEPTH} nodes      : {(t3 - t2) * 1e3:8.2f} ms")
        print(f"[perf] cycle detect (deep, rejected) : {(t4 - t3) * 1e3:8.2f} ms")

    def test_path_query_timing(self):
        f = Forest()
        build_chain(f, self.DEPTH, "p")
        deepest = f"p{self.DEPTH - 1}"

        rounds = 200
        t0 = time.perf_counter()
        for _ in range(rounds):
            path = f.path_to(deepest)
        t1 = time.perf_counter()
        self.assertEqual(len(path), self.DEPTH)
        self.assertEqual(path[0], "p0")
        self.assertEqual(path[-1], deepest)
        per_query_us = (t1 - t0) / rounds * 1e6
        print(f"\n[perf] path_to on {self.DEPTH}-deep chain: "
              f"{per_query_us:8.1f} us/query ({rounds} rounds)")

    def test_batch_scale(self):
        # 5k chains of depth 4 under one root, then batch-move 5k subtrees.
        f = Forest()
        f.add_node("root")
        n_moves = 5_000
        for i in range(n_moves):
            for j in range(4):
                nid = f"s{i}_{j}"
                f.add_node(nid, "root" if j == 0 else f"s{i}_{j - 1}")
        f.validate()

        moves = [(f"s{i}_1", f"s{(i * 7 + 3) % n_moves}_0")
                 for i in range(n_moves)]
        t0 = time.perf_counter()
        results = f.move_batch(moves)
        t1 = time.perf_counter()
        count, _ = f.validate()
        t2 = time.perf_counter()

        self.assertEqual(count, 1 + n_moves * 4)
        ok = sum(1 for r in results if r.ok)
        rejected = [r for r in results if not r.ok]
        print(f"\n[perf] batch of {n_moves} moves on 20k-node tree: "
              f"{(t1 - t0) * 1e3:8.2f} ms "
              f"({ok} applied, {len(rejected)} rejected)")
        print(f"[perf] validate after batch           : "
              f"{(t2 - t1) * 1e3:8.2f} ms")
        for r in rejected[:3]:
            print(f"[sample] rejected {r.node_id} -> {r.target_id}, "
                  f"conflict path length {len(r.conflict_path)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
