"""Self-tests for coverage-based test prioritization.

Covers: greedy gain ordering, deterministic tie-breaking, order
independence, the cumulative curve, and boundary cases (full overlap,
disjoint sets, zero-gain tests, empty inputs).
"""

import itertools
import unittest

from prioritize import cumulative_curve, prioritize


def permutations_of(coverage):
    """Yield every insertion-order permutation of a coverage mapping."""
    items = list(coverage.items())
    for perm in itertools.permutations(items):
        yield dict(perm)


class TestPrioritize(unittest.TestCase):
    def test_disjoint_sets_ordered_by_gain_then_name(self):
        # Disjoint: marginal gain == own size, so largest set wins each step;
        # equal size breaks lexicographically.
        coverage = {
            "t_big": {1, 2, 3, 4},
            "t_a": {5, 6, 7},
            "t_c": {8, 9, 10},
            "t_tiny": {11},
        }
        self.assertEqual(
            prioritize(coverage),
            ["t_big", "t_a", "t_c", "t_tiny"],
        )

    def test_full_overlap(self):
        # Every test covers the same lines. First pick is lexicographically
        # smallest id (sizes equal); the rest follow lexicographically with
        # zero gain.
        coverage = {
            "z_test": {1, 2, 3},
            "a_test": {1, 2, 3},
            "m_test": {1, 2, 3},
        }
        order = prioritize(coverage)
        self.assertEqual(order, ["a_test", "m_test", "z_test"])
        curve = cumulative_curve(coverage, order)
        self.assertEqual([row["gain"] for row in curve], [3, 0, 0])
        self.assertEqual([row["covered"] for row in curve], [3, 3, 3])
        self.assertTrue(all(row["pct"] == 100.0 for row in curve[1:]))

    def test_zero_gain_tests_go_last_deterministically(self):
        coverage = {
            "zzz_empty": set(),
            "aaa_empty": set(),
            "t_real": {1, 2},
            "mmm_empty": set(),
        }
        self.assertEqual(
            prioritize(coverage),
            ["t_real", "aaa_empty", "mmm_empty", "zzz_empty"],
        )

    def test_empty_mapping(self):
        self.assertEqual(prioritize({}), [])
        self.assertEqual(cumulative_curve({}), [])

    def test_all_cover_nothing(self):
        # No coverable lines anywhere: order is purely lexicographic and
        # pct reports 100.0 (there is nothing to cover).
        coverage = {"b": set(), "a": set(), "c": set()}
        self.assertEqual(prioritize(coverage), ["a", "b", "c"])
        curve = cumulative_curve(coverage)
        self.assertEqual([row["pct"] for row in curve], [100.0, 100.0, 100.0])
        self.assertEqual([row["gain"] for row in curve], [0, 0, 0])

    def test_tie_break_total_size_before_name(self):
        # First pick: t_wide has gain 4 vs t_narrow gain 2. Afterwards both
        # remaining tests have gain 0, and the larger-total-size rule makes
        # t_narrow precede the smaller one even though its name sorts later.
        coverage = {
            "_covered_first": {1},
            "t_narrow": {1, 2},
            "t_wide": {1, 2, 3, 4},
        }
        self.assertEqual(
            prioritize(coverage),
            ["t_wide", "t_narrow", "_covered_first"],
        )

    def test_tie_break_equal_gain_and_size_uses_name(self):
        # After b_wide (gain 4), a_narrow and c_x both add exactly one new
        # line and have equal total size: lexicographic id decides.
        coverage = {
            "a_narrow": {1, 2},
            "b_wide": {1, 3, 4, 5},
            "c_x": {1, 6},
        }
        self.assertEqual(
            prioritize(coverage),
            ["b_wide", "a_narrow", "c_x"],
        )

    def test_order_independence_all_permutations(self):
        # The same coverage *sets* in every possible insertion order must
        # produce exactly the same order.
        coverage = {
            "t_login": {1, 2, 3, 4, 5},
            "t_logout": {4, 5, 6},
            "t_upload": {2, 3, 7, 8, 9, 10},
            "t_download": {7, 8},
            "t_empty": set(),
        }
        expected = prioritize(coverage)
        curves = []
        for shuffled in permutations_of(coverage):
            self.assertEqual(prioritize(shuffled), expected)
            curves.append(cumulative_curve(shuffled))
        # The curve is also independent of input ordering.
        for curve in curves[1:]:
            self.assertEqual(curve, curves[0])

    def test_order_independence_reversed(self):
        coverage = {
            "a": {1}, "b": {1, 2}, "c": {2, 3}, "d": set(), "e": {3, 4, 5},
        }
        forward = prioritize(coverage)
        reverse = prioritize(dict(reversed(list(coverage.items()))))
        self.assertEqual(forward, reverse)
        self.assertEqual(forward, ["e", "b", "c", "a", "d"])

    def test_cumulative_curve_matches_union(self):
        coverage = {
            "a": {1, 2},
            "b": {2, 3, 4},
            "c": {5},
            "d": {1, 2, 3, 4, 5},
        }
        order = prioritize(coverage)
        curve = cumulative_curve(coverage, order)
        union = set()
        prev_pct = -1.0
        for row, tid in zip(curve, order):
            expected_gain = len(set(coverage[tid]) - union)
            union |= set(coverage[tid])
            self.assertEqual(row["gain"], expected_gain)
            self.assertEqual(row["covered"], len(union))
            self.assertEqual(row["test"], tid)
            self.assertEqual(row["rank"], curve.index(row) + 1)
            self.assertGreaterEqual(row["pct"], prev_pct)
            prev_pct = row["pct"]
        self.assertEqual(union, {1, 2, 3, 4, 5})
        self.assertEqual(curve[-1]["covered"], 5)
        self.assertEqual(curve[-1]["pct"], 100.0)

    def test_cumulative_curve_top(self):
        coverage = {"a": {1}, "b": {2, 3}, "c": set()}
        curve = cumulative_curve(coverage, prioritize(coverage), top=2)
        self.assertEqual(len(curve), 2)
        self.assertEqual([row["rank"] for row in curve], [1, 2])

    def test_iterable_line_inputs_accepted(self):
        # Lines may be any iterable, not just sets.
        coverage = {"a": [1, 2, 2, 3], "b": (2, 4)}
        self.assertEqual(prioritize(coverage), ["a", "b"])
        curve = cumulative_curve(coverage)
        self.assertEqual([row["gain"] for row in curve], [3, 1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
