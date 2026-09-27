"""lexkth 的自测：与暴力排序对拍 + 边界用例。

运行：python3 -m unittest test_lexkth -v   或   python3 test_lexkth.py
"""

import itertools
import random
import unittest

from lexkth import (kth_permutation, kth_combination,
                    count_permutations, count_combinations)


def brute_permutations(items):
    return [list(t) for t in sorted(set(itertools.permutations(items)))]


def brute_combinations_no_rep(items, r):
    return [list(t) for t in
            sorted(set(itertools.combinations(sorted(items), r)))]


def brute_combinations_with_rep(items, r):
    values = sorted(set(items))
    return [list(t) for t in
            sorted(set(itertools.combinations_with_replacement(values, r)))]


class BruteForceCrossCheck(unittest.TestCase):
    """小规模随机多重集上，对每个合法 k 逐一与暴力排序结果比对。"""

    def test_permutations_exhaustive(self):
        rng = random.Random(20260928)
        for _ in range(60):
            n = rng.randint(0, 7)
            items = [rng.randint(0, 3) for _ in range(n)]
            expected = brute_permutations(items)
            self.assertEqual(count_permutations(items), len(expected), items)
            for k in range(1, len(expected) + 1):
                self.assertEqual(kth_permutation(items, k), expected[k - 1],
                                 f"items={items} k={k}")

    def test_permutations_strings(self):
        items = list("aabbc")
        expected = brute_permutations(items)
        for k in range(1, len(expected) + 1):
            self.assertEqual(kth_permutation(items, k), expected[k - 1])

    def test_combinations_no_repetition_exhaustive(self):
        rng = random.Random(20260928)
        for _ in range(40):
            n = rng.randint(0, 7)
            items = [rng.randint(0, 3) for _ in range(n)]
            for r in range(0, n + 2):
                expected = brute_combinations_no_rep(items, r)
                self.assertEqual(count_combinations(items, r), len(expected),
                                 (items, r))
                for k in range(1, len(expected) + 1):
                    self.assertEqual(kth_combination(items, k, r),
                                     expected[k - 1],
                                     f"items={items} r={r} k={k}")

    def test_combinations_with_repetition_exhaustive(self):
        rng = random.Random(20260928)
        for _ in range(40):
            n = rng.randint(1, 6)
            items = [rng.randint(0, 3) for _ in range(n)]
            for r in range(0, 6):
                expected = brute_combinations_with_rep(items, r)
                self.assertEqual(
                    count_combinations(items, r, allow_repetition=True),
                    len(expected), (items, r))
                for k in range(1, len(expected) + 1):
                    self.assertEqual(
                        kth_combination(items, k, r, allow_repetition=True),
                        expected[k - 1], f"items={items} r={r} k={k}")


class EdgeCases(unittest.TestCase):
    def test_k_equals_1_and_max(self):
        items = [3, 1, 2, 1, 3]
        total = count_permutations(items)
        self.assertEqual(kth_permutation(items, 1), sorted(items))
        self.assertEqual(kth_permutation(items, total),
                         sorted(items, reverse=True))
        self.assertEqual(kth_combination(items, 1, 3), [1, 1, 2])
        last = count_combinations(items, 3)
        self.assertEqual(kth_combination(items, last, 3), [2, 3, 3])
        self.assertEqual(kth_combination(items, 1, 4, allow_repetition=True),
                         [1, 1, 1, 1])
        last = count_combinations(items, 4, allow_repetition=True)
        self.assertEqual(
            kth_combination(items, last, 4, allow_repetition=True),
            [3, 3, 3, 3])

    def test_all_elements_identical(self):
        items = [7, 7, 7, 7]
        self.assertEqual(count_permutations(items), 1)
        self.assertEqual(kth_permutation(items, 1), [7, 7, 7, 7])
        self.assertEqual(count_combinations(items, 3), 1)
        self.assertEqual(kth_combination(items, 1, 3), [7, 7, 7])
        self.assertEqual(kth_combination(items, 1, 2, allow_repetition=True),
                         [7, 7])

    def test_huge_space_near_int64_limit(self):
        items = [1] * 15 + [2] * 15 + [3] * 15
        total = count_permutations(items)
        self.assertGreater(total, 2 ** 62)
        self.assertEqual(kth_permutation(items, 1), sorted(items))
        self.assertEqual(kth_permutation(items, total),
                         sorted(items, reverse=True))
        mid = kth_permutation(items, total // 2 + 1)
        self.assertEqual(sorted(mid), sorted(items))
        big = list(range(50))
        total_c = count_combinations(big, 50, allow_repetition=True)
        self.assertGreater(total_c, 2 ** 63)
        self.assertEqual(kth_combination(big, 1, 50, allow_repetition=True),
                         [0] * 50)
        self.assertEqual(
            kth_combination(big, total_c, 50, allow_repetition=True),
            [49] * 50)
        prev = None
        for k in range(1, total_c, max(1, total_c // 200)):
            cur = kth_combination(big, k, 50, allow_repetition=True)
            if prev is not None:
                self.assertLessEqual(prev, cur)
            prev = cur

    def test_k_out_of_range_raises_with_valid_range(self):
        items = [1, 1, 2]
        total = count_permutations(items)
        for bad_k in (0, -1, total + 1, total + 100):
            with self.assertRaises(ValueError) as ctx:
                kth_permutation(items, bad_k)
            self.assertIn(f"1..{total}", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            kth_combination(items, 0, 2)
        self.assertIn("1..", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            kth_combination(items, 5, 2, allow_repetition=True)
        self.assertIn("1..", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            kth_combination([1, 2], 1, 3)
        self.assertIn("0", str(ctx.exception))

    def test_r_zero_and_empty_input(self):
        self.assertEqual(kth_combination([1, 2, 3], 1, 0), [])
        self.assertEqual(kth_permutation([], 1), [])
        with self.assertRaises(ValueError):
            kth_combination([], 1, 1, allow_repetition=True)

    def test_result_is_list_of_original_elements(self):
        items = ["b", "a", "b", "c"]
        self.assertEqual(kth_permutation(items, 2), ["a", "b", "c", "b"])
        self.assertEqual(kth_combination(items, 1, 2), ["a", "b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
