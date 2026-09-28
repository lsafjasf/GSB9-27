"""自测：边界情形 + 穷举对拍 + 随机对拍 + 性能基准（纯标准库，python3 -m unittest）。"""

import math
import random
import time
import unittest

from mst import brute_force_msf, kruskal_msf, prim_msf


def _check_forest(testcase: unittest.TestCase, result, n: int, m: int):
    """通用不变量：边数 == n - components；无环；覆盖全部顶点。"""

    testcase.assertEqual(len(result.edges), n - result.components)
    testcase.assertEqual(result.num_vertices, n)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    weight = 0
    for a, b, w in result.edges:
        ra, rb = find(a), find(b)
        testcase.assertNotEqual(ra, rb, "森林中出现环")
        parent[rb] = ra
        weight += w
    testcase.assertEqual(weight, result.total_weight)
    return weight


class TestSpecialCases(unittest.TestCase):
    def test_single_vertex(self):
        for algo in (kruskal_msf, prim_msf, brute_force_msf):
            r = algo(1, [])
            self.assertEqual(r.edges, [])
            self.assertEqual(r.total_weight, 0)
            self.assertEqual(r.components, 1)

    def test_empty_graph(self):
        for algo in (kruskal_msf, prim_msf, brute_force_msf):
            r = algo(5, [])
            self.assertEqual(r.edges, [])
            self.assertEqual(r.total_weight, 0)
            self.assertEqual(r.components, 5)

    def test_self_loops(self):
        edges = [(0, 0, 1), (1, 1, -7), (0, 1, 3), (2, 2, 0)]
        for algo in (kruskal_msf, prim_msf, brute_force_msf):
            r = algo(3, edges)
            self.assertEqual(r.edges, [(0, 1, 3)])
            self.assertEqual(r.total_weight, 3)
            self.assertEqual(r.components, 2)

    def test_parallel_edges(self):
        # (0,1) 有 5 / 1 / 5 三条重边，必须取权值 1 的那条
        edges = [(0, 1, 5), (0, 1, 1), (1, 0, 5), (1, 2, 2)]
        rk = kruskal_msf(3, edges)
        rp = prim_msf(3, edges)
        self.assertEqual(rk.edges, rp.edges)
        self.assertEqual(rk.edges, [(0, 1, 1), (1, 2, 2)])
        self.assertEqual(rk.total_weight, 3)
        self.assertEqual(rk.components, 1)

    def test_all_equal_weights_deterministic(self):
        # 所有边等权：唯一解由 (w, idx) 全序（即输入下标）确定
        edges = [(0, 2, 4), (0, 1, 4), (1, 2, 4), (2, 3, 4), (0, 3, 4)]
        rk = kruskal_msf(4, edges)
        rp = prim_msf(4, edges)
        rb = brute_force_msf(4, edges)
        # 扰动 w+idx*eps 下：idx 0,1,3 构成生成树（idx 2、4 成环被拒）
        self.assertEqual(rk.edges, [(0, 1, 4), (0, 2, 4), (2, 3, 4)])
        self.assertEqual(rp.edges, rk.edges)
        self.assertEqual(rb.edges, rk.edges)
        self.assertEqual(rk.total_weight, 12)

    def test_tie_break_by_input_order(self):
        # 权值相同但只有先出现的边能在不环的前提下入选
        edges = [(0, 1, 2), (1, 2, 2), (0, 2, 3), (0, 2, 2)]
        rk = kruskal_msf(3, edges)
        rp = prim_msf(3, edges)
        rb = brute_force_msf(3, edges)
        self.assertEqual(rk.edges, [(0, 1, 2), (1, 2, 2)])
        self.assertEqual(rp.edges, rk.edges)
        self.assertEqual(rb.edges, rk.edges)

    def test_negative_weights(self):
        edges = [(0, 1, -5), (1, 2, -3), (0, 2, -10)]
        rk = kruskal_msf(3, edges)
        rp = prim_msf(3, edges)
        rb = brute_force_msf(3, edges)
        self.assertEqual(rk.edges, [(0, 2, -10), (0, 1, -5)])
        self.assertEqual(rp.edges, rk.edges)
        self.assertEqual(rb.edges, rk.edges)
        self.assertEqual(rk.total_weight, -15)

    def test_disconnected_forest_semantics(self):
        # 三个连通分量：{0,1,2}、{3,4}、{5}，含自环与等权边
        edges = [(0, 1, 1), (1, 2, 2), (0, 2, 2), (3, 4, 7), (5, 5, 9)]
        for algo in (kruskal_msf, prim_msf, brute_force_msf):
            r = algo(6, edges)
            _check_forest(self, r, 6, 0)
            self.assertEqual(r.components, 3)
            self.assertEqual(sorted(r.edges), [(0, 1, 1), (1, 2, 2), (3, 4, 7)])
            self.assertEqual(r.total_weight, 10)

    def test_float_weights(self):
        edges = [(0, 1, 0.5), (1, 2, 1.5), (0, 2, 2.5)]
        rk = kruskal_msf(3, edges)
        rp = prim_msf(3, edges)
        rb = brute_force_msf(3, edges)
        self.assertTrue(math.isclose(rk.total_weight, 2.0))
        self.assertEqual(rk.edges, rp.edges)
        self.assertEqual(rb.edges, rk.edges)

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            kruskal_msf(3, [(0, 3, 1)])
        with self.assertRaises(ValueError):
            prim_msf(2, [(0, 1, float("nan"))])
        with self.assertRaises(ValueError):
            kruskal_msf(-1, [])
        with self.assertRaises(ValueError):
            kruskal_msf(3, [(0, 1)])

    def test_zero_vertices(self):
        for algo in (kruskal_msf, prim_msf, brute_force_msf):
            r = algo(0, [])
            self.assertEqual((r.edges, r.total_weight, r.components), ([], 0, 0))


class TestBruteForce(unittest.TestCase):
    def test_exhaustive_small_graphs(self):
        # 枚举全部简单图：n=1..4 的完全图边集（至多 6 条），每个子集 + 等权/混合权
        for n in range(1, 5):
            pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
            for mask in range(1 << len(pairs)):
                base = [pairs[k] for k in range(len(pairs)) if mask >> k & 1]
                for variant in range(3):
                    if variant == 0:
                        edges = [(u, v, 1) for u, v in base]              # 全等权
                    elif variant == 1:
                        edges = [(u, v, (k % 3) + 1) for k, (u, v) in enumerate(base)]
                    else:
                        edges = [(u, v, (k * k) % 7 - 3) for k, (u, v) in enumerate(base)]
                    rk = kruskal_msf(n, edges)
                    rp = prim_msf(n, edges)
                    rb = brute_force_msf(n, edges)
                    _check_forest(self, rk, n, 0)
                    self.assertEqual(rk.total_weight, rb.total_weight)
                    self.assertEqual(rp.total_weight, rb.total_weight)
                    self.assertEqual(rk.components, rb.components)
                    self.assertEqual(rk.edges, rp.edges)  # 确定性规则下逐边一致
                    self.assertEqual(rk.edges, rb.edges)  # 与 (权,秩) 字典序最优一致

    def test_random_with_loops_parallels(self):
        rng = random.Random(20260928)
        for _ in range(400):
            n = rng.randint(1, 6)
            edges = []
            for _ in range(rng.randint(0, 10)):
                u, v = rng.randrange(n), rng.randrange(n)
                w = rng.randint(-3, 5)
                edges.append((u, v, w))  # 含自环、重边
            rk = kruskal_msf(n, edges)
            rp = prim_msf(n, edges)
            rb = brute_force_msf(n, edges)
            _check_forest(self, rk, n, 0)
            self.assertEqual(rk.total_weight, rb.total_weight)
            self.assertEqual(rp.total_weight, rb.total_weight)
            self.assertEqual(rk.edges, rb.edges)
            self.assertEqual(rp.edges, rb.edges)


def _random_graph(n: int, extra: int, rng: random.Random, weight_span: int = 100):
    edges = [(i, rng.randrange(i + 1), rng.randrange(weight_span)) for i in range(1, n)]
    for _ in range(extra):
        u, v = rng.sample(range(n), 2)
        edges.append((u, v, rng.randrange(weight_span)))
    rng.shuffle(edges)
    return edges


class TestLargeAndBenchmark(unittest.TestCase):
    def test_ten_thousand_vertices(self):
        rng = random.Random(42)
        edges = _random_graph(10_000, 20_000, rng)
        t0 = time.perf_counter()
        rk = kruskal_msf(10_000, edges)
        tk = time.perf_counter() - t0
        t0 = time.perf_counter()
        rp = prim_msf(10_000, edges)
        tp = time.perf_counter() - t0
        _check_forest(self, rk, 10_000, 0)
        self.assertEqual(rk.components, 1)
        self.assertEqual(len(rk.edges), 9_999)
        self.assertEqual(rk.total_weight, rp.total_weight)
        self.assertEqual(rk.edges, rp.edges)
        print(f"\n[bench] n=10000, m=30000  Kruskal={tk * 1e3:.1f} ms  Prim={tp * 1e3:.1f} ms  "
              f"总权值={rk.total_weight}")

    def test_large_equal_weights_disconnected(self):
        rng = random.Random(7)
        n = 10_000
        edges = _random_graph(n, 2 * n, rng)
        # 再加 500 个孤立点；全部权值改为相同值以压测等权确定规则
        n_total = n + 500
        edges = [(u, v, 1) for u, v, _ in edges]
        t0 = time.perf_counter()
        rk = kruskal_msf(n_total, edges)
        tk = time.perf_counter() - t0
        t0 = time.perf_counter()
        rp = prim_msf(n_total, edges)
        tp = time.perf_counter() - t0
        _check_forest(self, rk, n_total, 0)
        self.assertEqual(rk.components, 501)
        self.assertEqual(rk.total_weight, n - 1)
        self.assertEqual(rk.edges, rp.edges)
        print(f"\n[bench] n={n_total}, m={len(edges)}, 全等权, 不连通  "
              f"Kruskal={tk * 1e3:.1f} ms  Prim={tp * 1e3:.1f} ms")


if __name__ == "__main__":
    unittest.main(verbosity=2)
