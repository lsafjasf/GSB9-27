"""Correctness tests: KD-tree vs brute-force reference, plus edge cases.

Run: python3 -m unittest test_kdtree -v   (or: python3 test_kdtree.py)
"""

import random
import unittest

from kdtree import KDTree, brute_force_topk


def rand_points(rng, n, dim):
    return [tuple(rng.random() for _ in range(dim)) for _ in range(n)]


class TestAgainstBruteForce(unittest.TestCase):
    """Randomized differential testing: tree results must equal brute force
    exactly (same points, same distances, same order)."""

    def check(self, points, queries, ks, build_balanced):
        if build_balanced:
            tree = KDTree.build(points)
        else:
            tree = KDTree(len(points[0]))
            for p in points:
                tree.insert(p)
        for q in queries:
            for k in ks:
                got = tree.query(q, k)
                want = brute_force_topk(points, q, k)
                self.assertEqual(len(got), len(want), f"k={k}")
                for (dg, pg), (dw, pw) in zip(got, want):
                    self.assertEqual(dg, dw)  # identical floats: same formula
                    self.assertEqual(pg, pw)

    def test_fuzz_continuous(self):
        for dim in (1, 2, 3, 5, 8, 16):
            for n in (1, 2, 10, 100, 1000):
                rng = random.Random(dim * 10000 + n)
                points = rand_points(rng, n, dim)
                queries = rand_points(rng, 10, dim)
                ks = [1, 3, min(10, n), n, n + 5]
                with self.subTest(dim=dim, n=n, mode="balanced"):
                    self.check(points, queries, ks, build_balanced=True)
                with self.subTest(dim=dim, n=n, mode="incremental"):
                    self.check(points, queries, ks, build_balanced=False)

    def test_fuzz_duplicates_and_ties(self):
        # Coarse integer grid -> many duplicate points and tied distances.
        for dim in (2, 3, 4):
            rng = random.Random(9000 + dim)
            points = [tuple(rng.randint(0, 3) for _ in range(dim))
                      for _ in range(400)]
            queries = [tuple(rng.randint(0, 3) for _ in range(dim))
                       for _ in range(20)]
            with self.subTest(dim=dim):
                self.check(points, queries, ks=[1, 5, 50, 400, 500],
                           build_balanced=True)
                self.check(points, queries, ks=[1, 5, 50, 400, 500],
                           build_balanced=False)


class TestInsertDelete(unittest.TestCase):
    def test_delete_matches_model(self):
        rng = random.Random(42)
        dim, n = 4, 600
        # Integer coords -> duplicates exist; model is a multiset (list).
        model = [tuple(rng.randint(0, 5) for _ in range(dim)) for _ in range(n)]
        tree = KDTree(dim)
        for p in model:
            tree.insert(p)
        self.assertEqual(len(tree), n)

        # Delete ~2/3 of the entries (one occurrence at a time), verifying
        # top-k against the model after every batch.
        victims = list(model)
        rng.shuffle(victims)
        victims = victims[: 2 * n // 3]
        for i, p in enumerate(victims):
            self.assertTrue(tree.delete(p))
            model.remove(p)  # removes one occurrence
            if i % 50 == 0 or i == len(victims) - 1:
                self.assertEqual(len(tree), len(model))
                for _ in range(5):
                    q = tuple(rng.randint(0, 5) for _ in range(dim))
                    self.assertEqual(tree.query(q, 7), brute_force_topk(model, q, 7))

        # Deleting a point that is not present must fail and change nothing.
        absent = tuple(100 + i for i in range(dim))
        self.assertFalse(tree.delete(absent))
        self.assertEqual(len(tree), len(model))

        # Drain the tree completely.
        for p in list(model):
            self.assertTrue(tree.delete(p))
        self.assertEqual(len(tree), 0)
        self.assertEqual(tree.query((0,) * dim, 5), [])
        self.assertFalse(tree.delete((0,) * dim))

        # Tree stays usable after being emptied.
        p = (1.0,) * dim
        tree.insert(p)
        self.assertEqual(tree.query((1.0,) * dim, 1), [(0.0, p)])

    def test_delete_duplicate_one_at_a_time(self):
        dim = 3
        p = (2.0, 2.0, 2.0)
        tree = KDTree(dim)
        for _ in range(5):
            tree.insert(p)
        tree.insert((0.0, 0.0, 0.0))
        for remaining in (5, 4, 3, 2, 1):
            self.assertEqual(tree.query(p, 10).count((0.0, p)), remaining)
            self.assertTrue(tree.delete(p))
        self.assertFalse(tree.delete(p))
        self.assertEqual(len(tree), 1)


class TestEdgeCases(unittest.TestCase):
    def test_single_point(self):
        tree = KDTree(3)
        tree.insert((1.0, 2.0, 3.0))
        self.assertEqual(tree.query((0.0, 0.0, 0.0), 1),
                         [(14.0 ** 0.5, (1.0, 2.0, 3.0))])
        self.assertEqual(tree.query((0.0, 0.0, 0.0), 10),
                         [(14.0 ** 0.5, (1.0, 2.0, 3.0))])

    def test_k_larger_than_size(self):
        points = [(float(i),) for i in range(4)]
        tree = KDTree.build(points)
        got = tree.query((0.0,), 100)
        self.assertEqual(got, brute_force_topk(points, (0.0,), 100))
        self.assertEqual(len(got), 4)

    def test_k_zero_and_negative(self):
        tree = KDTree.build([(1.0, 1.0), (2.0, 2.0)])
        self.assertEqual(tree.query((0.0, 0.0), 0), [])
        self.assertEqual(tree.query((0.0, 0.0), -3), [])

    def test_query_point_in_data(self):
        rng = random.Random(7)
        points = rand_points(rng, 300, 5)
        tree = KDTree.build(points)
        for p in points[:20]:
            top = tree.query(p, 1)[0]
            self.assertEqual(top[0], 0.0)  # exact hit: distance zero
            self.assertEqual(top[1], p)

    def test_all_points_identical(self):
        p = (3.0, 3.0)
        tree = KDTree(2)
        for _ in range(50):
            tree.insert(p)
        got = tree.query(p, 10)
        self.assertEqual(got, [(0.0, p)] * 10)
        _, visited = tree.query_with_stats(p, 10)
        self.assertLessEqual(visited, 50)

    def test_dimension_mismatch_rejected(self):
        tree = KDTree(3)
        with self.assertRaises(ValueError):
            tree.insert((1.0, 2.0))
        with self.assertRaises(ValueError):
            tree.query((1.0, 2.0), 1)
        with self.assertRaises(ValueError):
            tree.delete((1.0, 2.0))

    def test_empty_tree(self):
        tree = KDTree(2)
        self.assertEqual(tree.query((0.0, 0.0), 5), [])
        self.assertFalse(tree.delete((1.0, 1.0)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
