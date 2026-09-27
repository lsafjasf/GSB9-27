#!/usr/bin/env python3
"""Regression tests: skew repro, fix effectiveness, migration consistency.

Run: python3 -m unittest -v test_partitioning
"""

import random
import unittest

from partitioning import (
    BucketMigration,
    Cluster,
    DemoteMigration,
    HotKeyDetector,
    ModuloRouter,
    ShardedRouter,
    SplitMigration,
    assert_placement_consistent,
    distribution_metrics,
    run_workload,
    shard_storage_key,
    throughput_metrics,
)


def make_keys(n, prefix="key"):
    return [f"{prefix}-{i}" for i in range(n)]


def hot_key_stream(keys, n_requests, hot_key, hot_share, rng):
    others = [k for k in keys if k != hot_key]
    return [hot_key if (rng.random() < hot_share or not others)
            else rng.choice(others) for _ in range(n_requests)]


class SkewReproTest(unittest.TestCase):
    def setUp(self):
        self.keys = make_keys(2000)
        self.stream = hot_key_stream(self.keys, 60_000, "key-7", 0.40,
                                     random.Random(42))

    def test_repro_skew_modulo_router(self):
        cluster = Cluster(ModuloRouter(8))
        run_workload(cluster, self.stream, rng=random.Random(1))
        m = distribution_metrics(cluster.req_counts)
        self.assertGreater(m["max/mean"], 3.0)
        self.assertLess(m["effective_partitions"], 7.0)
        t = throughput_metrics(cluster.req_counts)
        self.assertLess(t["served_ratio"], 0.80)

    def test_fix_reduces_skew_and_restores_throughput(self):
        cluster = Cluster(ShardedRouter(8, num_buckets=256))
        run_workload(cluster, self.stream, detector=HotKeyDetector(),
                     window=6_000, rng=random.Random(1))
        m = distribution_metrics(cluster.req_counts)
        self.assertLess(m["max/mean"], 1.6)
        self.assertGreater(m["effective_partitions"], 6.5)
        t = throughput_metrics(cluster.req_counts)
        self.assertGreaterEqual(t["served_ratio"], 0.99)
        assert_placement_consistent(cluster)


class SemanticsTest(unittest.TestCase):
    def test_cold_key_always_same_partition(self):
        router = ShardedRouter(8, num_buckets=256)
        router2 = ShardedRouter(8, num_buckets=256)
        for key in make_keys(200):
            loc = router.read_locations(key)
            self.assertEqual(len(loc), 1)
            self.assertEqual(loc, router.read_locations(key))
            self.assertEqual(loc, router2.read_locations(key))

    def test_split_key_scatter_gather_read_your_writes(self):
        cluster = Cluster(ShardedRouter(8, num_buckets=256))
        SplitMigration(cluster, "hot", 8).run()
        for i in range(100):
            cluster.write("hot", i)
            self.assertEqual(cluster.read("hot"), i)
        assert_placement_consistent(cluster)

    def test_split_spreads_shards_across_partitions(self):
        router = ShardedRouter(8, num_buckets=256)
        router.hot["hot"] = 8
        parts = {p for p, _ in router.read_locations("hot")}
        self.assertEqual(len(parts), 8)


class EdgeCaseTest(unittest.TestCase):
    def test_all_keys_identical(self):
        stream = ["only-key"] * 20_000
        before = Cluster(ModuloRouter(8))
        run_workload(before, stream, rng=random.Random(2))
        mb = distribution_metrics(before.req_counts)
        self.assertEqual(mb["effective_partitions"], 1.0)
        self.assertEqual(mb["max"], mb["total"])

        after = Cluster(ShardedRouter(8, num_buckets=256))
        run_workload(after, stream, detector=HotKeyDetector(), window=2_000,
                     rng=random.Random(2))
        ma = distribution_metrics(after.req_counts)
        self.assertGreater(ma["effective_partitions"], 4.0)
        self.assertLess(ma["max/mean"], 2.5)
        self.assertIsNotNone(after.read("only-key"))
        assert_placement_consistent(after)

    def test_fewer_keys_than_partitions(self):
        keys = make_keys(3)
        stream = hot_key_stream(keys, 24_000, "key-0", 0.70, random.Random(7))
        before = Cluster(ModuloRouter(8))
        run_workload(before, stream, rng=random.Random(3))
        mb = distribution_metrics(before.req_counts)
        self.assertLessEqual(mb["effective_partitions"], 3.0)
        self.assertEqual(mb["median"], 0)

        after = Cluster(ShardedRouter(8, num_buckets=256))
        run_workload(after, stream, detector=HotKeyDetector(), window=3_000,
                     rng=random.Random(3))
        ma = distribution_metrics(after.req_counts)
        self.assertGreater(ma["effective_partitions"], mb["effective_partitions"])
        self.assertLess(ma["max/mean"], mb["max/mean"])
        assert_placement_consistent(after)

    def test_drifting_hot_key(self):
        keys = make_keys(200)
        after = Cluster(ShardedRouter(8, num_buckets=256))
        detector = HotKeyDetector()
        previous_hot = None
        for phase in range(4):
            hot_key = keys[phase]
            stream = hot_key_stream(keys, 12_000, hot_key, 0.50,
                                    random.Random(100 + phase))
            before_counts = list(after.req_counts)
            run_workload(after, stream, detector=detector, window=2_000,
                         rng=random.Random(phase))
            phase_counts = [x - y for x, y in zip(after.req_counts, before_counts)]
            m = distribution_metrics(phase_counts)
            self.assertLess(m["max/mean"], 2.0, f"phase {phase} still skewed")
            self.assertIn(hot_key, after.router.hot)
            if previous_hot is not None:
                self.assertNotIn(previous_hot, after.router.hot,
                                 "cooled key should be demoted")
            previous_hot = hot_key
            assert_placement_consistent(after)


class MigrationTest(unittest.TestCase):
    def _cluster(self, n=500):
        router = ShardedRouter(8, num_buckets=64)
        cluster = Cluster(router)
        for i in range(n):
            cluster.write(f"k{i}", f"v{i}")
        return cluster

    def test_bucket_migration_moves_data_and_ownership(self):
        cluster = self._cluster()
        router = cluster.router
        bucket = 5
        src = router.bucket_table[bucket]
        dst = (src + 1) % 8
        mig = BucketMigration(cluster, bucket, dst)
        states = []
        while mig.state != BucketMigration.DONE:
            states.append(mig.step())
            assert_placement_consistent(cluster)
        self.assertEqual(states, [BucketMigration.FLIP, BucketMigration.CLEANUP,
                                  BucketMigration.DONE])
        self.assertEqual(router.bucket_table[bucket], dst)
        for i in range(500):
            self.assertEqual(cluster.read(f"k{i}"), f"v{i}")

    def test_bucket_migration_reentrant_under_crash_at_every_step(self):
        for crash_before in range(3):
            cluster = self._cluster()
            router = cluster.router
            bucket = 5
            dst = (router.bucket_table[bucket] + 1) % 8
            mig = BucketMigration(cluster, bucket, dst)
            step_no = 0
            while mig.state != BucketMigration.DONE:
                if step_no == crash_before:
                    mig.recover()
                mig.step()
                assert_placement_consistent(cluster)
                step_no += 1
            self.assertEqual(mig.state, BucketMigration.DONE)
            mig.run()
            self.assertEqual(mig.state, BucketMigration.DONE)
            for i in range(500):
                self.assertEqual(cluster.read(f"k{i}"), f"v{i}")
            assert_placement_consistent(cluster)

    def test_split_migration_no_dual_ownership_during_crash(self):
        for crash_before in range(3):
            cluster = self._cluster()
            cluster.write("hot", "original")
            mig = SplitMigration(cluster, "hot", 8)
            step_no = 0
            while mig.state != SplitMigration.DONE:
                if step_no == crash_before:
                    mig.recover()
                mig.step()
                assert_placement_consistent(cluster)
                owners = [pid for pid, p in enumerate(cluster.partitions)
                          if "hot" in p]
                if "hot" not in cluster.router.hot:
                    self.assertEqual(len(owners), 1,
                                     "plain cell must have exactly one owner")
                else:
                    self.assertEqual(owners, [],
                                     "plain cell must be gone once split")
                self.assertEqual(cluster.read("hot"), "original")
                step_no += 1
            self.assertEqual(mig.state, SplitMigration.DONE)
            mig.run()
            self.assertEqual(cluster.read("hot"), "original")
            cluster.write("hot", "updated")
            self.assertEqual(cluster.read("hot"), "updated")
            assert_placement_consistent(cluster)

    def test_demote_migration_merges_back_to_single_owner(self):
        cluster = self._cluster()
        SplitMigration(cluster, "hot", 8).run()
        for i in range(50):
            cluster.write("hot", i)
        mig = DemoteMigration(cluster, "hot")
        while mig.state != DemoteMigration.DONE:
            mig.step()
            assert_placement_consistent(cluster)
        self.assertNotIn("hot", cluster.router.hot)
        owners = [pid for pid, p in enumerate(cluster.partitions) if "hot" in p]
        self.assertEqual(len(owners), 1)
        self.assertEqual(cluster.read("hot"), 49)
        for i in range(50, 60):
            cluster.write("hot", i)
            self.assertEqual(cluster.read("hot"), i)

    def test_demote_migration_reentrant_under_crash(self):
        for crash_before in range(3):
            cluster = self._cluster()
            SplitMigration(cluster, "hot", 8).run()
            cluster.write("hot", "latest")
            mig = DemoteMigration(cluster, "hot")
            step_no = 0
            while mig.state != DemoteMigration.DONE:
                if step_no == crash_before:
                    mig.recover()
                mig.step()
                assert_placement_consistent(cluster)
                step_no += 1
            self.assertEqual(mig.state, DemoteMigration.DONE)
            self.assertEqual(cluster.read("hot"), "latest")

    def test_detector_promotes_and_demotes(self):
        cluster = Cluster(ShardedRouter(8, num_buckets=256))
        detector = HotKeyDetector()
        hot_stream = hot_key_stream(make_keys(100), 10_000, "key-1", 0.5,
                                    random.Random(11))
        run_workload(cluster, hot_stream, detector=detector, window=10_000,
                     rng=random.Random(12))
        self.assertIn("key-1", cluster.router.hot)
        cold_stream = hot_key_stream(make_keys(100), 10_000, "key-2", 0.5,
                                     random.Random(13))
        run_workload(cluster, cold_stream, detector=detector, window=10_000,
                     rng=random.Random(14))
        self.assertNotIn("key-1", cluster.router.hot)
        self.assertIn("key-2", cluster.router.hot)
        assert_placement_consistent(cluster)


class MetricsTest(unittest.TestCase):
    def test_zero_and_uniform_counts(self):
        m = distribution_metrics([0] * 8)
        self.assertEqual(m["max/median"], 1.0)
        self.assertEqual(m["effective_partitions"], 0.0)
        m = distribution_metrics([10] * 8)
        self.assertEqual(m["max/mean"], 1.0)
        self.assertAlmostEqual(m["effective_partitions"], 8.0)

    def test_throughput_model(self):
        t = throughput_metrics([100, 0, 0, 0], headroom=1.0)
        self.assertEqual(t["capacity_per_partition"], 25)
        self.assertEqual(t["served"], 25)
        self.assertAlmostEqual(t["served_ratio"], 0.25)

    def test_shard_storage_key_format(self):
        self.assertEqual(shard_storage_key("a", 3), "a#shard3")


if __name__ == "__main__":
    unittest.main()
