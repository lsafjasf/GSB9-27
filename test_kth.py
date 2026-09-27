"""自测：暴力排序对拍 + 边界 + 超大计数。

运行：
    python3 -m unittest test_kth.py -v
或：
    python3 test_kth.py
"""

import itertools
import random
import time
import unittest

from kth import (
    KthError,
    kth_combination,
    kth_multiset,
    kth_permutation,
    multiset_combination_count,
    multiset_permutation_count,
)


# ---------------------------------------------------------------------------
# 暴力参考实现（小规模）
# ---------------------------------------------------------------------------

def brute_combinations(elements, r):
    """字典序枚举所有互不相同的长度为 r 的多集合组合。"""
    return sorted(set(itertools.combinations(sorted(elements), r)))


def brute_permutations(elements):
    """字典序枚举所有互不相同的多集合全排列。"""
    return sorted(set(itertools.permutations(elements)))


def count_combinations(elements, r):
    return len(brute_combinations(elements, r))


def count_permutations(elements):
    return len(brute_permutations(elements))


# ---------------------------------------------------------------------------
# 对拍
# ---------------------------------------------------------------------------

class TestBruteForceComparison(unittest.TestCase):
    def fuzz_inputs(self):
        rng = random.Random(20260927)
        cases = []
        for trial in range(240):
            length = rng.randint(0, 7)
            alphabet = rng.choice([[0, 1, 2, 3], ["a", "b", "c"]])
            elements = [rng.choice(alphabet) for _ in range(length)]
            cases.append(elements)
        cases.extend([
            [], [1], [1, 1], [1, 1, 1],
            [1, 2], [1, 1, 2], [1, 1, 1, 2],
            [1, 2, 3], [1, 1, 2, 2, 3],
            [1, 1, 2, 2, 2, 3, 3], ["a", "a", "b"],
        ])
        return cases

    def test_permutations_against_brute(self):
        for elements in self.fuzz_inputs():
            expected = brute_permutations(elements)
            counts = {}
            for value in sorted(elements):
                counts[value] = counts.get(value, 0) + 1
            total_fn = multiset_permutation_count(list(counts.values()))
            self.assertEqual(total_fn, len(expected), elements)
            for k in range(1, len(expected) + 1):
                with self.subTest(elements=elements, k=k, kind="P"):
                    self.assertEqual(kth_permutation(elements, k), expected[k - 1])

    def test_combinations_against_brute(self):
        for elements in self.fuzz_inputs():
            n = len(elements)
            counts_map = {}
            for value in sorted(elements):
                counts_map[value] = counts_map.get(value, 0) + 1
            counts = list(counts_map.values())
            for r in range(n + 1):
                expected = brute_combinations(elements, r)
                total_fn = multiset_combination_count(counts, r)
                self.assertEqual(total_fn, len(expected), (elements, r))
                for k in range(1, len(expected) + 1):
                    with self.subTest(elements=elements, r=r, k=k):
                        self.assertEqual(
                            kth_combination(elements, r, k), expected[k - 1]
                        )

    def test_unified_dispatcher(self):
        elements = [1, 1, 2, 3]
        expected_p = brute_permutations(elements)
        expected_c = brute_combinations(elements, 3)
        self.assertEqual(kth_multiset(elements, 4, "permutation"), expected_p[3])
        self.assertEqual(kth_multiset(elements, 2, "combination", r=3),
                         expected_c[1])
        with self.assertRaises(KthError):
            kth_multiset(elements, 1, "combination")
        with self.assertRaises(KthError):
            kth_multiset(elements, 1, "bogus")


# ---------------------------------------------------------------------------
# 边界用例
# ---------------------------------------------------------------------------

class TestBoundaries(unittest.TestCase):
    def test_k_one_and_k_max(self):
        elements = [1, 1, 2, 2, 3]
        perms = brute_permutations(elements)
        combos = brute_combinations(elements, 4)
        self.assertEqual(kth_permutation(elements, 1), perms[0])
        self.assertEqual(kth_permutation(elements, len(perms)), perms[-1])
        self.assertEqual(kth_combination(elements, 4, 1), combos[0])
        self.assertEqual(kth_combination(elements, 4, len(combos)), combos[-1])

    def test_all_same_elements_permutation(self):
        elements = [7, 7, 7, 7, 7]
        self.assertEqual(multiset_permutation_count([5]), 1)
        self.assertEqual(kth_permutation(elements, 1), (7, 7, 7, 7, 7))
        with self.assertRaisesRegex(KthError, "合法范围为 1\\.\\.1"):
            kth_permutation(elements, 2)
        with self.assertRaisesRegex(KthError, "合法范围为 1\\.\\.1"):
            kth_permutation(elements, 0)

    def test_all_same_elements_combination(self):
        elements = ["x", "x", "x", "x"]
        for r in range(5):
            combos = brute_combinations(elements, r)
            self.assertEqual(len(combos), 1)
            self.assertEqual(kth_combination(elements, r, 1), combos[0])
        with self.assertRaisesRegex(KthError, "合法范围为 1\\.\\.1"):
            kth_combination(elements, 2, 2)

    def test_empty_and_zero_r(self):
        self.assertEqual(kth_permutation([], 1), ())
        self.assertEqual(kth_combination([1, 2], 0, 1), ())
        self.assertEqual(multiset_combination_count([0, 0], 0), 1)

    def test_distinct_elements_matches_factoradic(self):
        # 元素互异时，排列总数应为 n!；与 itertools 直接对齐。
        elements = [1, 2, 3, 4, 5, 6]
        expected = brute_permutations(elements)
        for k in (1, 2, 300, 400, 720):
            self.assertEqual(kth_permutation(elements, k), expected[k - 1])
        # 互异元素选 r 个：总数 C(n, r)
        counts = [1] * 6
        self.assertEqual(multiset_combination_count(counts, 3), 20)
        expected_c = brute_combinations(elements, 3)
        for k in (1, 20):
            self.assertEqual(kth_combination(elements, 3, k), expected_c[k - 1])

    def test_unbounded_repeated_combination_formula(self):
        # 每种值副本充足时，选 r 个等价有重复组合 C(m+r-1, r)。
        m, r = 5, 6
        counts = [r] * m
        from math import comb
        self.assertEqual(multiset_combination_count(counts, r),
                         comb(m + r - 1, r))
        elements = [v for v in range(m) for _ in range(r)]
        expected = brute_combinations(elements, r)
        self.assertEqual(len(expected), comb(m + r - 1, r))
        self.assertEqual(kth_combination(elements, r, 1), expected[0])
        self.assertEqual(kth_combination(elements, r, len(expected)),
                         expected[-1])

    def test_k_out_of_range_reports_legal_range(self):
        elements = [1, 1, 2]
        n_perms = count_permutations(elements)
        with self.assertRaisesRegex(KthError, rf"1\.\.{n_perms}"):
            kth_permutation(elements, 0)
        with self.assertRaisesRegex(KthError, rf"1\.\.{n_perms}"):
            kth_permutation(elements, n_perms + 1)
        with self.assertRaisesRegex(KthError, rf"1\.\.{n_perms}"):
            kth_permutation(elements, -3)
        n_combos = count_combinations(elements, 2)
        with self.assertRaisesRegex(KthError, rf"1\.\.{n_combos}"):
            kth_combination(elements, 2, n_combos + 1)

    def test_bad_arguments(self):
        with self.assertRaises(KthError):
            kth_permutation([1, 2], 1.5)
        with self.assertRaises(KthError):
            kth_permutation([1, 2], True)
        with self.assertRaises(KthError):
            kth_combination([1, 2], -1, 1)
        with self.assertRaises(KthError):
            kth_combination([1, 2], 3, 1)
        with self.assertRaises(KthError):
            kth_combination([1, 2], 1.0, 1)


# ---------------------------------------------------------------------------
# 组合空间巨大（计数远超 64 位上限）
# ---------------------------------------------------------------------------

class TestHugeSpaces(unittest.TestCase):
    U64_MAX = 2 ** 64 - 1
    U128_MAX = 2 ** 128 - 1

    def test_huge_permutation_count_and_unranking(self):
        # 200 个元素：100 个 0 + 100 个 1，排列数 C(200,100)。
        elements = [0] * 100 + [1] * 100
        total = multiset_permutation_count([100, 100])
        from math import comb
        self.assertEqual(total, comb(200, 100))
        self.assertGreater(total, self.U128_MAX)

        t0 = time.perf_counter()
        first = kth_permutation(elements, 1)
        last = kth_permutation(elements, total)
        mid1 = kth_permutation(elements, total // 2)
        mid2 = kth_permutation(elements, total // 2 + 1)
        self.assertLess(time.perf_counter() - t0, 10.0)

        self.assertEqual(first, tuple([0] * 100 + [1] * 100))
        self.assertEqual(last, tuple([1] * 100 + [0] * 100))
        self.assertEqual(len(mid1), 200)
        self.assertEqual(len(set(mid1)), 2)
        # total 为偶数：k=total/2 是以 0 开头块的最后一个，k=total/2+1
        # 是以 1 开头块的第一个，两块各占 total/2 个排列
        self.assertEqual(mid1[0], 0)
        self.assertEqual(mid2[0], 1)
        self.assertEqual(mid1.count(0), 100)
        self.assertEqual(mid2.count(1), 100)
        self.assertLess(mid1, mid2)

        with self.assertRaisesRegex(KthError, rf"1\.\.{total}"):
            kth_permutation(elements, total + 1)

    def test_huge_permutation_many_distinct(self):
        # 40 个互不相同元素：40!，远超 64 位。
        elements = list(range(40))
        total = multiset_permutation_count([1] * 40)
        from math import factorial
        self.assertEqual(total, factorial(40))
        self.assertGreater(total, self.U64_MAX)
        first = kth_permutation(elements, 1)
        last = kth_permutation(elements, total)
        giant = kth_permutation(elements, self.U64_MAX + 7)
        self.assertEqual(first, tuple(range(40)))
        self.assertEqual(last, tuple(range(39, -1, -1)))
        self.assertEqual(sorted(giant), elements)
        self.assertNotEqual(giant, first)

    def test_huge_combination_count_and_unranking(self):
        # 200 个不同元素选 100：C(200,100) > 2^128。
        elements = list(range(200))
        counts = [1] * 200
        total = multiset_combination_count(counts, 100)
        from math import comb
        self.assertEqual(total, comb(200, 100))
        self.assertGreater(total, self.U128_MAX)

        t0 = time.perf_counter()
        first = kth_combination(elements, 100, 1)
        last = kth_combination(elements, 100, total)
        mid = kth_combination(elements, 100, total // 2)
        self.assertLess(time.perf_counter() - t0, 10.0)

        self.assertEqual(first, tuple(range(100)))
        self.assertEqual(last, tuple(range(100, 200)))
        self.assertEqual(len(mid), 100)
        self.assertEqual(len(set(mid)), 100)
        self.assertTrue(all(mid[i] < mid[i + 1] for i in range(99)))
        self.assertNotEqual(mid, first)

        with self.assertRaisesRegex(KthError, rf"1\.\.{total}"):
            kth_combination(elements, 100, total + 1)

    def test_huge_multiset_combination_with_repeats(self):
        # 带副本的大空间：50 种值，每种 6 个，选 80 个。
        m, c, r = 50, 6, 80
        counts = [c] * m
        total = multiset_combination_count(counts, r)
        self.assertGreater(total, self.U64_MAX)
        elements = [v for v in range(m) for _ in range(c)]
        first = kth_combination(elements, r, 1)
        last = kth_combination(elements, r, total)
        near = kth_combination(elements, r, total - 1)
        self.assertEqual(len(first), r)
        self.assertEqual(len(last), r)
        self.assertLess(first, near)
        self.assertLess(near, last)
        for answer in (first, near, last):
            for v in set(answer):
                self.assertLessEqual(answer.count(v), c)

    def test_count_near_64_bit_boundary_exact(self):
        # 取一个恰好落在 64 位边界附近的计数，验证不存在溢出截断。
        # C(66,33) < 2^64 < C(68,34)；跨边界的计数仍可精确定位。
        from math import comb
        self.assertLess(comb(66, 33), self.U64_MAX + 1)
        elements = list(range(68))
        total = multiset_combination_count([1] * 68, 34)
        self.assertEqual(total, comb(68, 34))
        self.assertGreater(total, self.U64_MAX)
        answer = kth_combination(elements, 34, self.U64_MAX)
        self.assertEqual(len(answer), 34)
        self.assertEqual(len(set(answer)), 34)


if __name__ == "__main__":
    unittest.main(verbosity=2)
