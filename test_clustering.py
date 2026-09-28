"""Self-tests for the deterministic clustering library (stdlib unittest)."""

import random
import unittest

from clustering import kmeans, choose_k, predict


def make_blobs(n_per=60, dim=4, k=3, spread=1.0, seed=7, gap=30.0):
    rng = random.Random(seed)
    centers = [[(c + 1) * gap * (1 if j % 2 == 0 else -1)
                for j in range(dim)] for c in range(k)]
    pts = []
    for c in range(k):
        for _ in range(n_per):
            pts.append([x + rng.gauss(0.0, spread) for x in centers[c]])
    rng.shuffle(pts)
    return pts


class TestDeterminism(unittest.TestCase):
    def test_repeated_runs_identical(self):
        pts = make_blobs()
        results = [kmeans(pts, 3) for _ in range(3)]
        for r in results[1:]:
            self.assertEqual(results[0].labels, r.labels)
            self.assertEqual(results[0].centers, r.centers)
            self.assertEqual(results[0].inertia, r.inertia)

    def test_choose_k_deterministic(self):
        pts = make_blobs()
        s1 = choose_k(pts, k_max=6).summary()
        s2 = choose_k(pts, k_max=6).summary()
        self.assertEqual(s1, s2)

    def test_warm_start_deterministic(self):
        # Simulates batch arrival: refit with previous centers as seeds.
        pts = make_blobs()
        half = len(pts) // 2
        r1 = kmeans(pts[:half], 3)
        refit_a = kmeans(pts, 3, init_centers=r1.centers)
        refit_b = kmeans(pts, 3, init_centers=r1.centers)
        self.assertEqual(refit_a.labels, refit_b.labels)
        self.assertEqual(refit_a.centers, refit_b.centers)


class TestEmptyClusters(unittest.TestCase):
    def test_all_identical_points(self):
        pts = [[1.0, 2.0]] * 50
        res = kmeans(pts, 3)
        self.assertEqual(res.labels, [0] * 50)      # everything in cluster 0
        self.assertEqual(res.empty_clusters, 2)     # two kept as empty
        self.assertGreater(res.empty_cluster_events, 0)  # and reported
        self.assertEqual(res.inertia, 0.0)

    def test_empty_events_reported_in_summary(self):
        pts = [[1.0, 2.0]] * 20
        s = kmeans(pts, 4).summary()
        self.assertIn("empty_cluster_events", s)
        self.assertEqual(s["empty_clusters_final"], 3)


class TestEdgeCases(unittest.TestCase):
    def test_fewer_points_than_k(self):
        pts = [[0.0], [5.0], [9.0]]
        res = kmeans(pts, 10)
        self.assertEqual(res.k, 3)          # clamped to n
        self.assertTrue(res.clamped)
        self.assertEqual(res.requested_k, 10)
        self.assertEqual(sorted(res.labels), [0, 1, 2])  # each point alone

    def test_empty_dataset_raises(self):
        with self.assertRaises(ValueError):
            kmeans([], 3)
        with self.assertRaises(ValueError):
            choose_k([], 3)

    def test_choose_k_all_identical(self):
        sel = choose_k([[3.0, 3.0]] * 100)
        self.assertEqual(sel.k, 1)
        self.assertIn("identical", sel.reason)

    def test_outlier_gets_own_cluster(self):
        rng = random.Random(3)
        pts = [[rng.gauss(0, 0.5), rng.gauss(0, 0.5)] for _ in range(60)]
        pts += [[rng.gauss(20, 0.5), rng.gauss(20, 0.5)] for _ in range(60)]
        pts += [[500.0, 500.0]]                      # single far outlier
        sel = choose_k(pts, k_max=5)
        self.assertEqual(sel.k, 3)                   # 2 blobs + outlier
        res = kmeans(pts, sel.k)
        outlier_label = res.labels[-1]
        self.assertEqual(res.labels.count(outlier_label), 1)  # isolated

    def test_choose_k_recovers_three_blobs(self):
        pts = make_blobs(k=3)
        sel = choose_k(pts, k_max=6)
        self.assertEqual(sel.k, 3)
        self.assertEqual(sel.metric, "calinski_harabasz")
        self.assertTrue(sel.reason)


class TestPredict(unittest.TestCase):
    def test_predict_matches_refit_assignment(self):
        pts = make_blobs()
        res = kmeans(pts, 3)
        self.assertEqual(predict(res.centers, pts), res.labels)

    def test_predict_new_batch_deterministic(self):
        pts = make_blobs()
        res = kmeans(pts, 3)
        batch = make_blobs(n_per=10, seed=99)
        self.assertEqual(predict(res.centers, batch),
                         predict(res.centers, batch))


if __name__ == "__main__":
    unittest.main(verbosity=2)
