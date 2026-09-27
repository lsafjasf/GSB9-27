"""textwave 自测：边界情形 + 极值保留性质验证。运行: python3 test_textwave.py"""

import math
import random
import unittest

import textwave
from textwave import _row_of, bin_minmax, choose_range, render


def parse_grid(output, height):
    """从渲染输出中拆出字符网格（去掉表头与纵轴刻度）。"""
    lines = output.splitlines()
    assert len(lines) == height + 1, "行数应为 height+1（含表头）"
    return [line.split("|", 1)[1] for line in lines[1:]]


class EdgeCases(unittest.TestCase):
    def test_all_zeros(self):
        out = render([0.0] * 1000, width=40, height=10)
        self.assertIn("y-range=[-0.5, 0.5]", out)  # 退化量程被对称扩开
        grid = parse_grid(out, 10)
        self.assertTrue(any("#" in row for row in grid))

    def test_single_point(self):
        out = render([3.14], width=40, height=10)
        grid = parse_grid(out, 10)
        marks = [(r, c) for r, row in enumerate(grid)
                 for c, ch in enumerate(row) if ch != " "]
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0][1], 0)  # 唯一一点落在第 0 列

    def test_constant_sequence(self):
        out = render([2.5] * 500, width=40, height=10)
        self.assertIn("y-range=[1.25, 3.75]", out)

    def test_single_spike_fully_visible(self):
        # 全零 + 单尖峰：鲁棒范围退化，必须回退完整范围，尖峰顶到第一行
        data = [0.0] * 10000
        data[5000] = 100.0
        width = 50
        out = render(data, width=width, height=12)
        self.assertIn("data=[0, 100]", out)
        self.assertIn("y-range=[0, 100]", out)
        grid = parse_grid(out, 12)
        spike_col = 5000 * width // len(data)
        self.assertEqual(grid[0][spike_col], "#")       # 峰顶
        self.assertEqual(grid[11][spike_col], "#")      # 一直到谷底
        self.assertNotIn("clipped", out)

    def test_outlier_does_not_flatten(self):
        # 正弦 + 单个 1e6 离群点：范围应取自正弦本体，离群点被裁剪标记
        data = [math.sin(i * 0.1) for i in range(2000)]
        data[1000] = 1e6
        out = render(data, width=60, height=12)
        self.assertIn("clipped: 1 col(s) above", out)
        self.assertIn("data=[-1, 1e+06]", out)
        self.assertIn("^", out)  # 越界标记
        # 显示范围应接近 [-1, 1] 而非 [−1, 1e6]
        header = out.splitlines()[0]
        yrange = header.split("y-range=[")[1].split("]")[0]
        hi = float(yrange.split(",")[1])
        self.assertLess(hi, 2.0)

    def test_long_sequence(self):
        data = [math.sin(i * 0.001) for i in range(1_000_000)]
        out = render(data, width=120, height=24)
        lines = out.splitlines()
        self.assertEqual(len(lines), 25)
        body_w = len(lines[1].split("|", 1)[1])
        self.assertEqual(body_w, 120)  # 等宽

    def test_iterator_input(self):
        out = render(iter([1.0, 2.0, 3.0]), width=10, height=6)
        self.assertIn("n=3", out)

    def test_validation(self):
        with self.assertRaises(ValueError):
            render([], width=10, height=5)
        with self.assertRaises(ValueError):
            render([1.0], width=0, height=5)
        with self.assertRaises(ValueError):
            render([1.0], width=10, height=1)


class ExtremesPreserved(unittest.TestCase):
    """性质验证：每一列渲染出的竖直跨度必须覆盖该列区间的 min 与 max。"""

    def check(self, data, width, height):
        out = render(data, width=width, height=height, clip_percentile=0)
        grid = parse_grid(out, height)
        cols_min, cols_max = bin_minmax(data, width)
        lo, hi, full_lo, full_hi, _ = choose_range(cols_min, cols_max, 0)
        self.assertEqual((lo, hi), (full_lo, full_hi))  # pct=0 不裁剪
        for c in range(width):
            if cols_min[c] is None:
                continue
            r_max = _row_of(cols_max[c], lo, hi, height)
            r_min = _row_of(cols_min[c], lo, hi, height)
            self.assertNotEqual(grid[r_max][c], " ",
                                f"列{c}最大值未体现")
            self.assertNotEqual(grid[r_min][c], " ",
                                f"列{c}最小值未体现")

    def test_random_noise(self):
        rng = random.Random(42)
        data = [rng.uniform(-10, 10) for _ in range(5000)]
        self.check(data, width=50, height=20)

    def test_spike_train(self):
        rng = random.Random(7)
        data = [0.0] * 3000
        for _ in range(30):
            data[rng.randrange(3000)] = rng.uniform(50, 100)
        self.check(data, width=60, height=16)

    def test_narrow_bins(self):
        # 数据点少于列数：空桶留白，非空桶仍保留极值
        rng = random.Random(1)
        data = [rng.random() for _ in range(7)]
        self.check(data, width=20, height=10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
