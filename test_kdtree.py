"""KDTree 自测：边界用例 + 与暴力扫描的随机对拍。

运行：python3 test_kdtree.py [-v]
"""

import random
import unittest

from kdtree import KDTree, brute_force


def assert_query_matches(tc, tree, live_points, q, k):
    """KD 树结果必须与暴力扫描逐条一致（pid 集合与距离都一致）。"""
    got = tree.query(q, k)
    want = brute_force(live_points, q, k)
    tc.assertEqual(len(got), len(want))
    for (g_pid, _g_pt, g_d), (w_pid, w_d) in zip(got, want):
        tc.assertEqual(g_pid, w_pid)
        tc.assertEqual(g_d, w_d)


class EdgeCaseTests(unittest.TestCase):
    def test_single_point(self):
        t = KDTree(3)
        pid = t.insert((1.0, 2.0, 3.0))
        got = t.query((1.0, 2.0, 3.0), 1)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], pid)
        self.assertEqual(got[0][2], 0.0)
        # K 大于数据量：返回全部
        self.assertEqual(len(t.query((9.0, 9.0, 9.0), 100)), 1)

    def test_empty_tree(self):
        t = KDTree(2)
        self.assertEqual(t.query((0.0, 0.0), 5), [])
        self.assertEqual(len(t), 0)

    def test_k_zero(self):
        t = KDTree(2)
        t.insert((0.0, 0.0))
        self.assertEqual(t.query((0.0, 0.0), 0), [])

    def test_duplicate_points(self):
        t = KDTree(2)
        pids = [t.insert((3.0, 4.0)) for _ in range(10)]
        t.insert((100.0, 100.0))
        got = t.query((3.0, 4.0), 10)
        # 10 个重复点距离全为 0，按 pid 升序决胜
        self.assertEqual([r[0] for r in got], pids)
        self.assertTrue(all(r[2] == 0.0 for r in got))
        # 删除部分重复点后仍正确
        for pid in pids[:5]:
            self.assertTrue(t.delete(pid))
        got = t.query((3.0, 4.0), 10)
        # k=10 大于存活数 6：返回全部（5 个重复点 + 1 个远点）
        self.assertEqual([r[0] for r in got], pids[5:] + [pids[-1] + 1])

    def test_query_point_equals_data_point(self):
        t = KDTree(4)
        pts = [(float(i),) * 4 for i in range(50)]
        for p in pts:
            t.insert(p)
        for p in pts:
            got = t.query(p, 1)
            self.assertEqual(got[0][2], 0.0)
            self.assertEqual(got[0][1], p)

    def test_k_greater_than_n(self):
        t = KDTree(2)
        pts = [(random.random(), random.random()) for _ in range(7)]
        for p in pts:
            t.insert(p)
        got = t.query((0.5, 0.5), 1000)
        self.assertEqual(len(got), 7)
        dists = [r[2] for r in got]
        self.assertEqual(dists, sorted(dists))

    def test_delete_all_then_query(self):
        t = KDTree(2)
        pids = [t.insert((float(i), float(i))) for i in range(20)]
        for pid in pids:
            self.assertTrue(t.delete(pid))
        self.assertEqual(len(t), 0)
        self.assertEqual(t.query((0.0, 0.0), 5), [])
        # 删除后重新插入仍正常
        pid = t.insert((1.0, 1.0))
        self.assertEqual(t.query((1.0, 1.0), 1)[0][0], pid)

    def test_delete_missing(self):
        t = KDTree(2)
        pid = t.insert((0.0, 0.0))
        self.assertFalse(t.delete(pid + 999))
        self.assertTrue(t.delete(pid))
        self.assertFalse(t.delete(pid))  # 重复删除

    def test_delete_point_by_value(self):
        t = KDTree(2)
        t.insert((1.0, 1.0))
        t.insert((1.0, 1.0))
        self.assertTrue(t.delete_point((1.0, 1.0)))
        self.assertEqual(len(t), 1)
        self.assertTrue(t.delete_point((1.0, 1.0)))
        self.assertFalse(t.delete_point((1.0, 1.0)))

    def test_dimension_mismatch(self):
        t = KDTree(3)
        with self.assertRaises(ValueError):
            t.insert((1.0, 2.0))
        t.insert((1.0, 2.0, 3.0))
        with self.assertRaises(ValueError):
            t.query((1.0, 2.0), 1)
        with self.assertRaises(ValueError):
            t.query((1.0, 2.0, 3.0), -1)


class RandomizedVsBruteForceTests(unittest.TestCase):
    def test_static_sets(self):
        rng = random.Random(20260927)
        for dim in (1, 2, 3, 5, 8, 16):
            for n in (1, 2, 10, 100, 500):
                t = KDTree(dim)
                pts = [tuple(rng.uniform(-10, 10) for _ in range(dim))
                       for _ in range(n)]
                # 掺入重复点
                pts += [pts[rng.randrange(n)] for _ in range(min(n, 20))]
                for p in pts:
                    t.insert(p)
                live = t.items()
                for _ in range(20):
                    q = tuple(rng.uniform(-10, 10) for _ in range(dim))
                    for k in (1, 3, 10, n, n + 30):
                        assert_query_matches(self, t, live, q, k)
                    # 查询点与数据点重合
                    p = live[rng.randrange(len(live))][1]
                    assert_query_matches(self, t, live, p, 5)

    def test_interleaved_insert_delete(self):
        rng = random.Random(12345)
        for dim in (1, 3, 7):
            t = KDTree(dim)
            live = {}  # pid -> point
            for step in range(300):
                if not live or rng.random() < 0.6:
                    if rng.random() < 0.15 and live:
                        p = live[rng.choice(list(live))]  # 重复点
                    else:
                        p = tuple(rng.uniform(-5, 5) for _ in range(dim))
                    pid = t.insert(p)
                    live[pid] = p
                else:
                    pid = rng.choice(list(live))
                    self.assertTrue(t.delete(pid))
                    del live[pid]
                if step % 25 == 0:
                    q = tuple(rng.uniform(-5, 5) for _ in range(dim))
                    k = rng.choice([1, 2, 5, 20, len(live) + 3])
                    assert_query_matches(self, t, list(live.items()), q, k)
            # 收尾全量校验
            for _ in range(10):
                q = tuple(rng.uniform(-5, 5) for _ in range(dim))
                assert_query_matches(self, t, list(live.items()), q, 8)

    def test_grid_ties(self):
        # 整数网格：大量等距点，专门考验决胜规则
        rng = random.Random(7)
        for dim in (1, 2, 3):
            t = KDTree(dim)
            grid = [tuple(float(c) for c in p)
                    for p in __import__("itertools").product(range(4), repeat=dim)]
            for p in grid:
                t.insert(p)
            live = t.items()
            for _ in range(30):
                q = tuple(rng.choice([0.0, 0.5, 1.0, 1.5, 2.0]) for _ in range(dim))
                for k in (1, 4, 9, len(grid), len(grid) + 2):
                    assert_query_matches(self, t, live, q, k)


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in __import__("sys").argv else 1)
