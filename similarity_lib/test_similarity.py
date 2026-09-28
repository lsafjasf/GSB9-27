"""性质断言测试 + 与朴素实现对拍。

运行：python3 test_similarity.py
"""

import math
import random
import unittest

import similarity as sim


# ------------------------------------------------------------- 朴素实现（对拍基准）
# 刻意用最直接的写法，不做任何数值稳定处理。

def naive_cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def naive_euclidean(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def naive_manhattan(a, b):
    return sum(abs(x - y) for x, y in zip(a, b))


def naive_jaccard(a, b):
    sa, sb = set(a), set(b)
    union = len(sa | sb)
    if union == 0:
        return 1.0
    return len(sa & sb) / union


# ------------------------------------------------------------- 数据生成

def dense_vector(rng, dim, lo=-10.0, hi=10.0):
    return [rng.uniform(lo, hi) for _ in range(dim)]


def sparse_vector(rng, dim, zero_ratio=0.9):
    """九成以上为零的稀疏向量（dict 表示）。"""
    vec = {}
    for i in range(dim):
        if rng.random() > zero_ratio:
            vec[i] = rng.uniform(-10.0, 10.0)
    return vec


def sparse_dense_pair(rng, dim, zero_ratio=0.9):
    """稀疏数据，但以稠密 list 形式给出（用于和朴素实现对拍）。"""
    return [
        0.0 if rng.random() < zero_ratio else rng.uniform(-10.0, 10.0)
        for _ in range(dim)
    ]


class TestRangesAndSemantics(unittest.TestCase):
    """返回范围与基本语义。"""

    def setUp(self):
        self.rng = random.Random(42)

    def test_cosine_range_and_sign(self):
        a = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(sim.cosine_similarity(a, a), 1.0)
        self.assertAlmostEqual(sim.cosine_similarity(a, [-x for x in a]), -1.0)
        self.assertAlmostEqual(sim.cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)
        for _ in range(200):
            x = dense_vector(self.rng, 8)
            y = dense_vector(self.rng, 8)
            self.assertTrue(-1.0 <= sim.cosine_similarity(x, y) <= 1.0)
            self.assertTrue(0.0 <= sim.cosine_distance(x, y) <= 2.0)

    def test_jaccard_range(self):
        self.assertEqual(sim.jaccard_similarity({1, 2}, {1, 2}), 1.0)
        self.assertEqual(sim.jaccard_similarity({1, 2}, {3, 4}), 0.0)
        self.assertAlmostEqual(sim.jaccard_similarity({1, 2, 3}, {2, 3, 4}), 0.5)
        for _ in range(200):
            sa = {self.rng.randrange(20) for _ in range(10)}
            sb = {self.rng.randrange(20) for _ in range(10)}
            self.assertTrue(0.0 <= sim.jaccard_similarity(sa, sb) <= 1.0)
            self.assertTrue(0.0 <= sim.jaccard_distance(sa, sb) <= 1.0)

    def test_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            sim.euclidean_distance([1.0, 2.0], [1.0])


class TestMetricProperties(unittest.TestCase):
    """可断言的数学性质：对称性、非负性、同一对象距离为零、三角不等式。"""

    def setUp(self):
        self.rng = random.Random(7)

    def _check_metric(self, dist, gen, trials=100, tol=1e-9):
        for _ in range(trials):
            a, b, c = gen(), gen(), gen()
            dab, dba = dist(a, b), dist(b, a)
            dbc, dac = dist(b, c), dist(a, c)
            # 非负性 + 有限性（不允许 inf/nan）
            for d in (dab, dbc, dac):
                self.assertTrue(math.isfinite(d), "距离必须为有限值")
                self.assertGreaterEqual(d, 0.0, "距离必须非负")
            # 对称性
            self.assertAlmostEqual(dab, dba, delta=tol * max(1.0, dab))
            # 同一对象距离为零
            self.assertEqual(dist(a, a), 0.0)
            # 三角不等式
            self.assertLessEqual(dac, dab + dbc + tol * max(1.0, dab, dbc),
                                 "三角不等式不成立")

    def test_euclidean_is_metric_dense(self):
        self._check_metric(sim.euclidean_distance,
                           lambda: dense_vector(self.rng, 16))

    def test_euclidean_is_metric_sparse(self):
        self._check_metric(sim.euclidean_distance,
                           lambda: sparse_vector(self.rng, 500))

    def test_manhattan_is_metric_dense(self):
        self._check_metric(sim.manhattan_distance,
                           lambda: dense_vector(self.rng, 16))

    def test_manhattan_is_metric_sparse(self):
        self._check_metric(sim.manhattan_distance,
                           lambda: sparse_vector(self.rng, 500))

    def test_jaccard_distance_is_metric(self):
        self._check_metric(
            sim.jaccard_distance,
            lambda: {self.rng.randrange(30) for _ in range(self.rng.randrange(15))},
        )

    def test_cosine_symmetry_and_identity(self):
        # 余弦相似度只断言对称性与自身为 1（余弦距离非度量，不断言三角不等式）
        for _ in range(100):
            a = dense_vector(self.rng, 16)
            b = dense_vector(self.rng, 16)
            self.assertAlmostEqual(sim.cosine_similarity(a, b),
                                   sim.cosine_similarity(b, a), places=12)
            self.assertAlmostEqual(sim.cosine_similarity(a, a), 1.0, places=12)


class TestNaiveCrossCheck(unittest.TestCase):
    """与朴素实现对拍：稠密 + 稀疏（九成以上为零）。"""

    def setUp(self):
        self.rng = random.Random(123)

    def test_dense_cross_check(self):
        for _ in range(300):
            a = dense_vector(self.rng, 32)
            b = dense_vector(self.rng, 32)
            self.assertAlmostEqual(sim.cosine_similarity(a, b),
                                   naive_cosine(a, b), places=10)
            self.assertAlmostEqual(sim.euclidean_distance(a, b),
                                   naive_euclidean(a, b), places=8)
            self.assertAlmostEqual(sim.manhattan_distance(a, b),
                                   naive_manhattan(a, b), places=8)

    def test_sparse_cross_check(self):
        for _ in range(300):
            a = sparse_dense_pair(self.rng, 1000, zero_ratio=0.95)
            b = sparse_dense_pair(self.rng, 1000, zero_ratio=0.95)
            self.assertAlmostEqual(sim.cosine_similarity(a, b),
                                   naive_cosine(a, b), places=10)
            self.assertAlmostEqual(sim.euclidean_distance(a, b),
                                   naive_euclidean(a, b), places=8)
            self.assertAlmostEqual(sim.manhattan_distance(a, b),
                                   naive_manhattan(a, b), places=8)
            # dict 稀疏表示与稠密表示结果一致
            da = {i: v for i, v in enumerate(a) if v != 0.0}
            db = {i: v for i, v in enumerate(b) if v != 0.0}
            self.assertAlmostEqual(sim.cosine_similarity(da, db),
                                   sim.cosine_similarity(a, b), places=12)
            self.assertAlmostEqual(sim.euclidean_distance(da, db),
                                   sim.euclidean_distance(a, b), places=12)

    def test_jaccard_cross_check(self):
        for _ in range(300):
            a = [self.rng.randrange(50) for _ in range(20)]
            b = [self.rng.randrange(50) for _ in range(20)]
            self.assertEqual(sim.jaccard_similarity(a, b), naive_jaccard(a, b))


class TestEdgeCases(unittest.TestCase):
    """零向量、极小范数、量纲差异、空集合。"""

    def test_zero_vectors(self):
        zero = [0.0, 0.0, 0.0]
        one = [1.0, 2.0, 3.0]
        self.assertEqual(sim.cosine_similarity(zero, one), 0.0)
        self.assertEqual(sim.cosine_similarity(zero, zero), 0.0)
        self.assertEqual(sim.cosine_distance(zero, one), 1.0)
        self.assertEqual(sim.euclidean_distance(zero, zero), 0.0)
        self.assertEqual(sim.manhattan_distance(zero, zero), 0.0)
        self.assertEqual(sim.jaccard_similarity([], []), 1.0)
        self.assertEqual(sim.jaccard_distance([], []), 0.0)

    def test_tiny_norm(self):
        tiny = [1e-300, -1e-300]
        # 不溢出、不除零，方向信息保留
        self.assertAlmostEqual(sim.cosine_similarity(tiny, tiny), 1.0, places=12)
        self.assertAlmostEqual(sim.cosine_similarity(tiny, [-1e-300, 1e-300]),
                               -1.0, places=12)
        d = sim.euclidean_distance(tiny, [0.0, 0.0])
        self.assertTrue(math.isfinite(d) and d > 0.0)

    def test_extreme_scale_difference(self):
        big = [1e150, 0.0]
        small = [0.0, 1e-150]
        # 余弦：方向正交，与量纲无关
        self.assertAlmostEqual(sim.cosine_similarity(big, small), 0.0, places=12)
        self.assertAlmostEqual(sim.cosine_similarity(big, [2e150, 0.0]), 1.0, places=12)
        # 距离：有限确定结果
        for d in (sim.euclidean_distance(big, small),
                  sim.manhattan_distance(big, small)):
            self.assertTrue(math.isfinite(d))

    def test_overflow_raises_not_inf(self):
        # 1e308 - (-1e308) 超出 float64，必须明确报错而非返回 inf
        with self.assertRaises(OverflowError):
            sim.euclidean_distance([1e308], [-1e308])
        with self.assertRaises(OverflowError):
            sim.manhattan_distance([1e308, 1e308], [-1e308, -1e308])

    def test_no_nan_inf_on_random(self):
        rng = random.Random(9)
        for _ in range(500):
            scale = 10.0 ** rng.randrange(-200, 200)
            a = [rng.uniform(-1, 1) * scale for _ in range(8)]
            b = [rng.uniform(-1, 1) * scale for _ in range(8)]
            for v in (sim.cosine_similarity(a, b),
                      sim.euclidean_distance(a, b),
                      sim.manhattan_distance(a, b)):
                self.assertTrue(math.isfinite(v), f"非有限结果: {v}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
