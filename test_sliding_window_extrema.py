"""边界用例单元测试：python3 -m unittest test_sliding_window_extrema -v"""

import unittest

from sliding_window_extrema import SlidingWindowExtrema
from brute_force import BruteForceExtrema


class TestEdgeCases(unittest.TestCase):
    def test_empty_structure(self):
        s = SlidingWindowExtrema(5)
        self.assertIsNone(s.min())
        self.assertIsNone(s.max())
        self.assertEqual(len(s), 0)

    def test_window_zero(self):
        s = SlidingWindowExtrema(0)
        s.push(1)
        s.push(2)
        self.assertEqual(len(s), 0)
        self.assertIsNone(s.min())
        self.assertIsNone(s.max())

    def test_window_one(self):
        s = SlidingWindowExtrema(1)
        for v in [5, 3, 9, 1]:
            s.push(v)
            self.assertEqual(s.min(), v)
            self.assertEqual(s.max(), v)
            self.assertEqual(len(s), 1)

    def test_window_larger_than_data(self):
        s = SlidingWindowExtrema(100)
        for v in [4, 2, 8]:
            s.push(v)
        self.assertEqual(len(s), 3)
        self.assertEqual(s.min(), 2)
        self.assertEqual(s.max(), 8)

    def test_equal_elements(self):
        s = SlidingWindowExtrema(3)
        for v in [7, 7, 7, 7, 7]:
            s.push(v)
        self.assertEqual(s.min(), 7)
        self.assertEqual(s.max(), 7)
        s.push(1)  # 窗口 [7, 7, 1]
        self.assertEqual(s.min(), 1)
        self.assertEqual(s.max(), 7)
        # 再推 3 个 7，让 1 滑出窗口
        s.push(7)
        s.push(7)
        s.push(7)
        self.assertEqual(s.min(), 7)
        self.assertEqual(s.max(), 7)

    def test_slide_expiry(self):
        s = SlidingWindowExtrema(3)
        for v in [1, 2, 3, 0, 5]:
            s.push(v)
        # 窗口为 [3, 0, 5]
        self.assertEqual(s.min(), 0)
        self.assertEqual(s.max(), 5)
        s.push(4)  # 窗口 [0, 5, 4]
        self.assertEqual(s.min(), 0)
        self.assertEqual(s.max(), 5)
        s.push(6)  # 窗口 [5, 4, 6]
        self.assertEqual(s.min(), 4)
        self.assertEqual(s.max(), 6)

    def test_resize_shrink(self):
        s = SlidingWindowExtrema(5)
        for v in [9, 1, 8, 2, 7]:
            s.push(v)
        s.resize(2)  # 窗口变为 [2, 7]
        self.assertEqual(len(s), 2)
        self.assertEqual(s.min(), 2)
        self.assertEqual(s.max(), 7)

    def test_resize_grow_reactivates_history(self):
        s = SlidingWindowExtrema(2)
        for v in [100, 50, 1, 2]:
            s.push(v)
        # 当前窗口 [1, 2]
        self.assertEqual(s.max(), 2)
        s.resize(4)  # 历史 100, 50 重新生效
        self.assertEqual(len(s), 4)
        self.assertEqual(s.min(), 1)
        self.assertEqual(s.max(), 100)

    def test_resize_grow_beyond_history(self):
        s = SlidingWindowExtrema(2)
        for v in [3, 1, 4]:
            s.push(v)
        s.resize(1000)
        self.assertEqual(len(s), 3)
        self.assertEqual(s.min(), 1)
        self.assertEqual(s.max(), 4)

    def test_resize_to_zero_and_back(self):
        s = SlidingWindowExtrema(3)
        for v in [5, 6, 7]:
            s.push(v)
        s.resize(0)
        self.assertIsNone(s.min())
        self.assertIsNone(s.max())
        self.assertEqual(len(s), 0)
        s.push(8)
        self.assertIsNone(s.min())  # 窗口仍为 0
        s.resize(2)  # 历史 [7, 8] 重新生效
        self.assertEqual(s.min(), 7)
        self.assertEqual(s.max(), 8)

    def test_resize_to_one(self):
        s = SlidingWindowExtrema(5)
        for v in [3, 1, 4, 1, 5]:
            s.push(v)
        s.resize(1)
        self.assertEqual(s.min(), 5)
        self.assertEqual(s.max(), 5)

    def test_push_after_resize(self):
        s = SlidingWindowExtrema(2)
        for v in [10, 20, 30]:
            s.push(v)
        s.resize(3)  # 窗口 [10, 20, 30]
        s.push(5)    # 窗口 [20, 30, 5]
        self.assertEqual(s.min(), 5)
        self.assertEqual(s.max(), 30)
        s.push(40)   # 窗口 [30, 5, 40]
        self.assertEqual(s.min(), 5)
        self.assertEqual(s.max(), 40)

    def test_invalid_window(self):
        for bad in [-1, 1.5, "3", None, True]:
            with self.assertRaises(ValueError):
                SlidingWindowExtrema(bad)
        s = SlidingWindowExtrema(3)
        with self.assertRaises(ValueError):
            s.resize(-2)

    def test_negative_and_float_values(self):
        s = SlidingWindowExtrema(4)
        for v in [-3.5, 2.0, -10, 7.25, 0]:
            s.push(v)
        self.assertEqual(s.min(), -10)
        self.assertEqual(s.max(), 7.25)

    def test_against_brute_fixed_sequence(self):
        seq = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9]
        for w in [0, 1, 2, 3, 5, 15, 100]:
            fast = SlidingWindowExtrema(w)
            slow = BruteForceExtrema(w)
            for v in seq:
                fast.push(v)
                slow.push(v)
                self.assertEqual(fast.min(), slow.min())
                self.assertEqual(fast.max(), slow.max())


if __name__ == "__main__":
    unittest.main()
