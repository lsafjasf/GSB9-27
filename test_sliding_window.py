"""Differential fuzz tests + explicit boundary cases.

Run:  python3 test_sliding_window.py
Exit code is non-zero on any mismatch/failure.
"""

import random
import unittest

from brute_force import BruteForceWindowMinMax
from sliding_window_extrema import SlidingWindowMinMax


def _assert_match(testcase, structure, reference):
    testcase.assertEqual(structure.window_size, reference.window_size)
    testcase.assertEqual(structure.count, reference.count)
    testcase.assertEqual(structure.active_count, reference.active_count)
    testcase.assertEqual(structure.get_min(), reference.get_min())
    testcase.assertEqual(structure.get_max(), reference.get_max())
    testcase.assertEqual(structure.get_min_max(), reference.get_min_max())


class FuzzDifferentialTests(unittest.TestCase):
    """Every step of a random push/resize stream must match brute force."""

    def _run_fuzz(self, seed, steps, max_window, value_domain,
                  resize_probability=0.25):
        rng = random.Random(seed)
        start = rng.randint(0, max_window)
        structure = SlidingWindowMinMax(start)
        reference = BruteForceWindowMinMax(start)
        for _ in range(steps):
            if rng.random() < resize_probability:
                new_size = rng.randint(0, max_window)
                structure.resize(new_size)
                reference.resize(new_size)
            else:
                value = rng.choice(value_domain)
                structure.push(value)
                reference.push(value)
            _assert_match(self, structure, reference)

    def test_small_windows_all_equal(self):
        # Tiny alphabet => long equal-value runs stress the "==" pop rule.
        for seed in range(200):
            self._run_fuzz(
                seed=seed, steps=120, max_window=6,
                value_domain=[1, 1, 1, 2, 2, 3], resize_probability=0.3,
            )

    def test_mid_windows_random_ints(self):
        for seed in range(60):
            self._run_fuzz(
                seed=1000 + seed, steps=400, max_window=20,
                value_domain=list(range(-8, 9)), resize_probability=0.2,
            )

    def test_large_windows_sparse_values(self):
        for seed in range(8):
            self._run_fuzz(
                seed=9000 + seed, steps=3000, max_window=500,
                value_domain=list(range(-50, 51)), resize_probability=0.15,
            )

    def test_window_zero_heavy(self):
        for seed in range(40):
            self._run_fuzz(
                seed=5000 + seed, steps=200, max_window=10,
                value_domain=[0, 1, 2], resize_probability=0.45,
            )

    def test_grow_shrink_cycles(self):
        # Mostly resize to 0 / large values interleaved with bursts of pushes.
        rng = random.Random(42)
        structure = SlidingWindowMinMax(0)
        reference = BruteForceWindowMinMax(0)
        for _ in range(300):
            action = rng.random()
            if action < 0.4:
                structure.resize(rng.choice([0, 1, 2, 5, 25, 100]))
                reference.resize(structure.window_size)
            else:
                value = rng.randint(0, 20)
                structure.push(value)
                reference.push(value)
            _assert_match(self, structure, reference)


class BoundaryTests(unittest.TestCase):
    """Explicitly documented behaviour for each required edge case."""

    def test_empty_window_returns_none(self):
        sw = SlidingWindowMinMax(4)
        self.assertIsNone(sw.get_min())
        self.assertIsNone(sw.get_max())
        self.assertEqual(sw.get_min_max(), (None, None))
        self.assertEqual(sw.active_count, 0)

    def test_window_larger_than_queued_count(self):
        sw = SlidingWindowMinMax(100)
        for value in (5, 2, 8):
            sw.push(value)
        self.assertEqual(sw.active_count, 3)
        self.assertEqual(sw.get_min_max(), (2, 8))

    def test_window_size_one(self):
        sw = SlidingWindowMinMax(1)
        for value in (3, 7, 7, 2, 9):
            sw.push(value)
            self.assertEqual(sw.get_min(), value)
            self.assertEqual(sw.get_max(), value)
        self.assertEqual(sw.get_min_max(), (9, 9))

    def test_window_size_zero(self):
        sw = SlidingWindowMinMax(0)
        sw.push(10)
        sw.push(20)
        self.assertEqual(sw.count, 2)
        self.assertEqual(sw.active_count, 0)
        self.assertEqual(sw.get_min_max(), (None, None))
        # History retained: growing makes old values effective again.
        sw.resize(2)
        self.assertEqual(sw.get_min_max(), (10, 20))

    def test_all_elements_equal(self):
        sw = SlidingWindowMinMax(3)
        for _ in range(10):
            sw.push(7)
            self.assertEqual(sw.get_min(), 7)
            self.assertEqual(sw.get_max(), 7)
        self.assertEqual(sw.active_count, 3)

    def test_equal_values_do_not_leave_stale_front(self):
        # "==" pop rule: an equal duplicate that later expires must not
        # shadow a newer equal copy after a window grows.
        sw = SlidingWindowMinMax(1)
        sw.push(5)
        sw.push(5)
        sw.push(5)
        sw.resize(3)
        self.assertEqual(sw.get_min_max(), (5, 5))

    def test_shrink_then_grow(self):
        sw = SlidingWindowMinMax(5)
        for value in (1, 2, 3, 4, 5, 6, 7):
            sw.push(value)
        sw.resize(2)
        self.assertEqual(sw.get_min_max(), (6, 7))
        sw.resize(5)
        self.assertEqual(sw.get_min_max(), (3, 7))
        sw.resize(0)
        self.assertEqual(sw.get_min_max(), (None, None))
        sw.resize(3)
        self.assertEqual(sw.get_min_max(), (5, 7))

    def test_push_after_shrink_and_grow(self):
        sw = SlidingWindowMinMax(4)
        for value in (4, 1, 9, 2):
            sw.push(value)
        sw.resize(1)
        self.assertEqual(sw.get_min_max(), (2, 2))
        sw.push(10)
        self.assertEqual(sw.get_min_max(), (10, 10))
        sw.resize(3)
        self.assertEqual(sw.get_min_max(), (min(9, 2, 10), max(9, 2, 10)))

    def test_resize_to_same_size_is_noop_safe(self):
        sw = SlidingWindowMinMax(3)
        for value in (3, 1, 4, 1, 5):
            sw.push(value)
        sw.resize(3)
        self.assertEqual(sw.get_min_max(), (1, 5))

    def test_invalid_window_sizes(self):
        with self.assertRaises(ValueError):
            SlidingWindowMinMax(-1)
        sw = SlidingWindowMinMax(2)
        with self.assertRaises(ValueError):
            sw.resize(-3)
        with self.assertRaises(TypeError):
            sw.resize(2.0)
        with self.assertRaises(TypeError):
            SlidingWindowMinMax(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
