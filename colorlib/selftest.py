"""selftest — colorlib 自测、往返误差评估与性能基准。

运行：python3 selftest.py
"""

import random
import sys
import time
import unittest

import colorlib as cl


# ================================================================ 单元测试
class EdgeCaseTests(unittest.TestCase):
    """边界用例集：全黑 / 全白 / 极暗 / 极亮 / 单通道 / 越界 / 非法输入。"""

    def test_black(self):
        self.assertEqual(cl.srgb8_to_linear((0, 0, 0)), (0.0, 0.0, 0.0))
        self.assertEqual(cl.linear_to_srgb8((0.0, 0.0, 0.0)), (0, 0, 0))
        self.assertEqual(cl.srgb8_to_hsv((0, 0, 0)), (0.0, 0.0, 0.0))
        self.assertEqual(cl.hsv_to_srgb8((0.0, 0.0, 0.0)), (0, 0, 0))
        self.assertEqual(cl.srgb8_to_xyz((0, 0, 0)), (0.0, 0.0, 0.0))

    def test_white(self):
        self.assertEqual(cl.srgb8_to_linear((255, 255, 255)), (1.0, 1.0, 1.0))
        self.assertEqual(cl.linear_to_srgb8((1.0, 1.0, 1.0)), (255, 255, 255))
        h, s, v = cl.srgb8_to_hsv((255, 255, 255))
        self.assertEqual((s, v), (0.0, 1.0))
        self.assertEqual(cl.hsv_to_srgb8((0.0, 0.0, 1.0)), (255, 255, 255))
        x, y, z = cl.srgb8_to_xyz((255, 255, 255))
        # D65 白点约 (0.9505, 1.0, 1.089)；矩阵取 4 位小数，Z 略低于理论值
        self.assertAlmostEqual(y, 1.0, places=4)

    def test_very_dark(self):
        # 极暗：1/255 落在线性段，往返必须无损
        for v in (1, 2, 3):
            lin = cl.srgb8_to_linear((v, v, v))
            self.assertAlmostEqual(lin[0], v / 255.0 / 12.92, places=12)
            self.assertEqual(cl.linear_to_srgb8(lin), (v, v, v))
        # 极暗单色
        self.assertEqual(cl.linear_to_srgb8(cl.srgb8_to_linear((0, 0, 1))), (0, 0, 1))

    def test_very_bright(self):
        for rgb in ((254, 255, 255), (255, 254, 255), (255, 255, 254), (253, 254, 255)):
            self.assertEqual(cl.linear_to_srgb8(cl.srgb8_to_linear(rgb)), rgb)

    def test_single_channel(self):
        for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255),
                    (1, 0, 0), (0, 1, 0), (0, 0, 1),
                    (128, 0, 0), (0, 0, 200)):
            self.assertEqual(cl.linear_to_srgb8(cl.srgb8_to_linear(rgb)), rgb)
        # 纯红 -> HSV(0,1,1)，纯绿 -> (120,1,1)，纯蓝 -> (240,1,1)
        self.assertEqual(cl.srgb8_to_hsv((255, 0, 0)), (0.0, 1.0, 1.0))
        self.assertEqual(cl.srgb8_to_hsv((0, 255, 0)), (120.0, 1.0, 1.0))
        self.assertEqual(cl.srgb8_to_hsv((0, 0, 255)), (240.0, 1.0, 1.0))
        self.assertEqual(cl.hsv_to_srgb8((120.0, 1.0, 1.0)), (0, 255, 0))

    def test_clamp_mode(self):
        self.assertEqual(cl.srgb8_to_linear((-5, 300, 128)),
                         cl.srgb8_to_linear((0, 255, 128)))
        self.assertEqual(cl.linear_to_srgb8((-0.2, 1.5, 0.5)), (0, 255, 188))
        self.assertEqual(cl.hsv_to_srgb8((720.0, 2.0, -1.0)), (0, 0, 0))  # h 取模, s/v 裁剪

    def test_strict_mode(self):
        with self.assertRaises(ValueError):
            cl.srgb8_to_linear((-1, 0, 0), mode="strict")
        with self.assertRaises(ValueError):
            cl.srgb8_to_linear((0, 0, 256), mode="strict")
        with self.assertRaises(ValueError):
            cl.linear_to_srgb8((1.01, 0.0, 0.0), mode="strict")
        with self.assertRaises(ValueError):
            cl.hsv_to_srgb8((0.0, 1.5, 1.0), mode="strict")

    def test_nan_and_garbage_rejected(self):
        nan = float("nan")
        for bad in ((nan, 0, 0), (0, nan, 0), (0, 0, nan)):
            with self.assertRaises(ValueError):
                cl.srgb8_to_linear(bad)
            with self.assertRaises(ValueError):
                cl.srgb8_to_linear(bad, mode="strict")
        with self.assertRaises(ValueError):
            cl.linear_to_srgb8((nan, 0.5, 0.5))
        with self.assertRaises(ValueError):
            cl.srgb8_to_linear(("red", 0, 0))
        with self.assertRaises(ValueError):
            cl.srgb8_to_linear((True, 0, 0))
        with self.assertRaises(ValueError):
            cl.srgb8_to_linear((1, 2))  # 通道数不足

    def test_roundtrip_gray_exhaustive(self):
        # 全部 256 级灰度：gamma 往返必须零误差
        for v in range(256):
            self.assertEqual(cl.linear_to_srgb8(cl.srgb8_to_linear((v, v, v))), (v, v, v))


# ================================================================ 误差评估
def _stats(errors):
    n = len(errors)
    return max(errors), sum(errors) / n


def evaluate_roundtrip(samples):
    """对样本做三条链路往返，返回 {链路: {通道: (max_err, mean_err)}}（8bit 单位）。"""
    chains = {
        "sRGB8->linear->sRGB8": lambda p: cl.linear_to_srgb8(cl.srgb8_to_linear(p)),
        "sRGB8->HSV->sRGB8": lambda p: cl.hsv_to_srgb8(cl.srgb8_to_hsv(p)),
        "sRGB8->XYZ->sRGB8": lambda p: cl.xyz_to_srgb8(cl.srgb8_to_xyz(p)),
    }
    results = {}
    for name, rt in chains.items():
        errs = [[], [], []]
        for p in samples:
            q = rt(p)
            for ch in range(3):
                errs[ch].append(abs(q[ch] - p[ch]))
        results[name] = {ch: _stats(errs[ch]) for ch in range(3)}
    return results


def run_evaluation(count=1_000_000, seed=42):
    rng = random.Random(seed)
    samples = [(rng.randrange(256), rng.randrange(256), rng.randrange(256))
               for _ in range(count)]
    # 额外加入暗部密集采样（暗部是 gamma 转换最敏感区域）
    samples += [(rng.randrange(16), rng.randrange(16), rng.randrange(16))
                for _ in range(count // 10)]
    print("=" * 72)
    print("往返误差评估：%d 随机样本 + %d 暗部样本 (seed=%d)" % (count, count // 10, seed))
    print("误差单位：8bit 灰阶 (0-255)")
    print("-" * 72)
    for name, per_ch in evaluate_roundtrip(samples).items():
        print(name)
        for ch, label in enumerate("RGB"):
            mx, mean = per_ch[ch]
            print("  通道 %s: 最大误差 = %.4f  平均误差 = %.6f" % (label, mx, mean))
    print()

    # 误差来源演示：中间结果若以 N bit 线性量化存储，往返即出现可见误差
    print("误差来源演示：线性中间值按 N bit 量化存储后的往返误差（100000 样本）")
    sub = samples[:100000]
    for bits in (16, 12, 10, 8):
        levels = (1 << bits) - 1
        errs = [[], [], []]
        for p in sub:
            lin = cl.srgb8_to_linear(p)
            q = tuple(round(c * levels) / levels for c in lin)  # 模拟 N bit 帧缓冲
            back = cl.linear_to_srgb8(q)
            for ch in range(3):
                errs[ch].append(abs(back[ch] - p[ch]))
        mx = max(max(e) for e in errs)
        mean = sum(sum(e) for e in errs) / (3 * len(sub))
        print("  线性中间值 %2d bit: 最大误差 = %d  平均误差 = %.6f" % (bits, mx, mean))
    print()


# ================================================================ 性能基准
def run_benchmark(count=1_000_000, seed=1):
    rng = random.Random(seed)
    pixels = [(rng.randrange(256), rng.randrange(256), rng.randrange(256))
              for _ in range(count)]
    print("=" * 72)
    print("性能基准：%d 像素 (CPython %s)" % (count, sys.version.split()[0]))
    print("-" * 72)
    cases = [
        ("sRGB8 -> linear", lambda p: cl.srgb8_to_linear(p)),
        ("linear -> sRGB8", None),  # 特殊处理：先转出线性数据
        ("sRGB8 -> HSV", lambda p: cl.srgb8_to_hsv(p)),
        ("HSV -> sRGB8", None),
        ("sRGB8 -> XYZ", lambda p: cl.srgb8_to_xyz(p)),
        ("XYZ -> sRGB8", None),
        ("往返 sRGB8->linear->sRGB8", lambda p: cl.linear_to_srgb8(cl.srgb8_to_linear(p))),
        ("往返 sRGB8->HSV->sRGB8", lambda p: cl.hsv_to_srgb8(cl.srgb8_to_hsv(p))),
    ]
    linear_pixels = [cl.srgb8_to_linear(p) for p in pixels]
    hsv_pixels = [cl.srgb8_to_hsv(p) for p in pixels]
    xyz_pixels = [cl.srgb8_to_xyz(p) for p in pixels]
    data_map = {
        "linear -> sRGB8": (linear_pixels, cl.linear_to_srgb8),
        "HSV -> sRGB8": (hsv_pixels, cl.hsv_to_srgb8),
        "XYZ -> sRGB8": (xyz_pixels, cl.xyz_to_srgb8),
    }
    for name, fn in cases:
        if fn is None:
            data, fn = data_map[name]
        else:
            data = pixels
        t0 = time.perf_counter()
        for p in data:
            fn(p)
        dt = time.perf_counter() - t0
        print("  %-28s %7.3f s   (%5.2f Mpx/s)" % (name, dt, count / dt / 1e6))
    print()


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(EdgeCaseTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
    run_evaluation()
    run_benchmark()
