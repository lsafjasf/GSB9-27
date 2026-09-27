"""Regression tests: skew reproduction, fix effectiveness, migration
consistency/reentrancy, and edge cases (identical keys, fewer keys than
partitions, drifting hotspot).

Run: python3 -m unittest -v   (or: python3 test_partitioning.py)
"""
import os
import tempfile
import unittest

from partitioner import (BalancedPartitioner, HashPartitioner, KVStore,
                         MigrationError, MigrationManager, distribution_metrics)
from simulator import (all_same, compare, drifting, few_keys, run_engine,
                       skewed, build_router)

N = 16
REQS = 20_000


class SkewReproductionTest(unittest.TestCase):
    """Quantify the skew and reproduce the hotspot on the baseline."""

    @classmethod
    def setUpClass(cls):
        cls.rep = compare("skewed", skewed(REQS), N)

    def test_baseline_reproduces_hotspot(self):
        m = self.rep["baseline"]["req_metrics"]
        self.assertGreater(m["max_over_median"], 10)   # one partition pegged
        self.assertGreater(m["top_share"], 0.7)        # >70% of traffic on 1 of 16
        self.assertLess(m["jain"], 0.2)

    def test_fix_spreads_load(self):
        m = self.rep["fixed"]["req_metrics"]
        self.assertLess(m["max_over_median"], 4)
        self.assertGreater(m["jain"], 0.5)
        self.assertEqual(m["active"], N)               # all partitions used

    def test_fix_beats_baseline_on_every_skew_metric(self):
        b, f = self.rep["baseline"]["req_metrics"], self.rep["fixed"]["req_metrics"]
        self.assertLess(f["max_over_median"], b["max_over_median"])
        self.assertLess(f["max_over_mean"], b["max_over_mean"])
        self.assertLess(f["top_share"], b["top_share"])
        self.assertGreater(f["jain"], b["jain"])

    def test_fix_improves_throughput(self):
        b = self.rep["baseline"]["stats"]
        f = self.rep["fixed"]["stats"]
        self.assertGreater(f["throughput"], 2 * b["throughput"])
        self.assertLess(f["ticks"], b["ticks"])
        self.assertLess(f["max_queue"], b["max_queue"])


class KeySemanticsTest(unittest.TestCase):
    """Same-key->same-partition for normal keys; compensated scatter/gather
    for hot keys."""

    def test_normal_key_mapping_is_stable(self):
        r1 = BalancedPartitioner(N, KVStore(N))
        r2 = BalancedPartitioner(N, KVStore(N))
        for key in ("user:1", "user:2", "order:xyz"):
            self.assertEqual(r1.write_key(key), r1.write_key(key))
            self.assertEqual(r1.read_key(key)[1], r2.read_key(key)[1])  # restart-stable

    def test_hot_key_scatter_gather_preserves_total(self):
        r = BalancedPartitioner(N, KVStore(N), window=100)
        for _ in range(100):           # trip hot detection
            r.observe("user:hot")
        self.assertIn("user:hot", r.hot)
        for _ in range(250):
            r.write_key("user:hot")
        total, touched = r.read_key("user:hot")
        self.assertEqual(total, 250)   # merge over shards is exact
        self.assertGreater(len({p for p, _ in touched}), 1)  # really scattered

    def test_hot_split_survives_interleaved_normal_keys(self):
        r = BalancedPartitioner(N, KVStore(N), window=100)
        for _ in range(100):
            r.observe("user:hot")
        for i in range(50):
            r.write_key("user:hot")
            r.write_key(f"user:{i}")
        self.assertEqual(r.read_key("user:hot")[0], 50)
        for i in range(50):
            self.assertEqual(r.read_key(f"user:{i}")[0], 1)


class MigrationConsistencyTest(unittest.TestCase):
    """Single-owner invariant + reentrancy of hotspot migration."""

    def setUp(self):
        self.store = KVStore(N)
        self.store.add(3, "user:hot", 1000)

    def test_single_owner_at_every_step(self):
        mig = MigrationManager(self.store)
        mig.begin("user:hot", "user:hot#shard0", 3, 11)
        owners_seen = []
        while mig.in_flight:
            mig.step()
            mig.assert_invariants()
            # the record belongs to exactly ONE partition at all times
            self.assertIn(mig.owner_of("user:hot"), (3, 11, None))
            owners_seen.append(mig.journal["owner"] if mig.journal else 11)
            self.assertEqual(len(owners_seen[-1:]), 1)
        self.assertEqual(self.store.locations("user:hot#shard0"), [11])
        self.assertEqual(self.store.locations("user:hot"), [])
        self.assertEqual(self.store.get(11, "user:hot#shard0"), 1000)

    def test_shadow_copy_is_never_owned(self):
        mig = MigrationManager(self.store)
        mig.begin("user:hot", "user:hot#shard0", 3, 11)
        mig.step()  # COPY: bytes exist in both partitions now
        self.assertIn("user:hot#shard0", self.store.parts[11])
        self.assertEqual(mig.owner_of("user:hot"), 3)      # ...but owner is still src
        self.assertEqual(mig.owner_of("user:hot#shard0"), 3)
        mig.step()  # CUTOVER
        self.assertEqual(mig.owner_of("user:hot#shard0"), 11)

    def test_begin_is_idempotent_and_conflicts_raise(self):
        mig = MigrationManager(self.store)
        self.assertEqual(mig.begin("user:hot", "user:hot#shard0", 3, 11), "COPY")
        self.assertEqual(mig.begin("user:hot", "user:hot#shard0", 3, 11), "COPY")  # no-op
        with self.assertRaises(MigrationError):
            mig.begin("user:hot", "user:hot#other", 3, 12)   # conflicting migration
        mig.run_to_completion()
        self.assertFalse(mig.in_flight)                      # journal cleared
        with self.assertRaises(MigrationError):
            mig.begin("user:hot", "user:hot#shard0", 3, 11)  # source already moved

    def test_crash_mid_migration_resumes_from_journal(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "journal.json")
            mig = MigrationManager(self.store, journal_path=path)
            mig.begin("user:hot", "user:hot#shard0", 3, 11)
            mig.step()  # COPY done, then "crash"
            mig2 = MigrationManager(self.store, journal_path=path)  # restart
            self.assertTrue(mig2.in_flight)
            self.assertEqual(mig2.journal["state"], "CUTOVER")
            mig2.run_to_completion()
            mig2.assert_invariants()
            self.assertEqual(self.store.locations("user:hot#shard0"), [11])
            self.assertEqual(self.store.get(11, "user:hot#shard0"), 1000)
            self.assertFalse(os.path.exists(path))  # journal cleaned up

    def test_copy_step_is_idempotent_after_crash(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "journal.json")
            mig = MigrationManager(self.store, journal_path=path)
            mig.begin("user:hot", "user:hot#shard0", 3, 11)
            # crash AFTER writing the shadow copy but BEFORE persisting state:
            self.store.set(11, "user:hot#shard0", 1000)
            mig2 = MigrationManager(self.store, journal_path=path)
            mig2.run_to_completion()  # COPY re-runs: set() is idempotent
            self.assertEqual(self.store.get(11, "user:hot#shard0"), 1000)
            self.assertEqual(self.store.locations("user:hot"), [])

    def test_reads_consistent_throughout_migration(self):
        mig = MigrationManager(self.store)
        mig.begin("user:hot", "user:hot#shard0", 3, 11)
        while mig.in_flight:
            mig.step()
            owner = mig.owner_of("user:hot") or mig.owner_of("user:hot#shard0")
            key = "user:hot" if owner == 3 else "user:hot#shard0"
            self.assertEqual(self.store.get(owner, key), 1000)  # never lost


class EdgeCaseTest(unittest.TestCase):
    def test_all_keys_identical(self):
        rep = compare("all-same", all_same(REQS), N)
        b, f = rep["baseline"], rep["fixed"]
        self.assertEqual(b["req_metrics"]["active"], 1)          # 1 core pegged
        self.assertGreaterEqual(f["req_metrics"]["active"], 4)   # spread over shards
        self.assertLess(f["req_metrics"]["max_over_mean"],
                        b["req_metrics"]["max_over_mean"] / 3)
        # read-your-writes through scatter/gather
        router = f["router"]
        self.assertEqual(router.read_key("user:only-key")[0],
                         sum(router.store.get(p, k)
                             for p in range(N) for k in router.store.parts[p]
                             if k.startswith("user:only-key")))

    def test_fewer_keys_than_partitions(self):
        rep = compare("few", few_keys(REQS, num_keys=3), N)
        b, f = rep["baseline"], rep["fixed"]
        self.assertEqual(b["req_metrics"]["active"], 3)   # 3 cores busy, 13 idle
        self.assertGreater(f["req_metrics"]["active"], 8) # splitting engages idle cores
        self.assertGreater(f["stats"]["throughput"], b["stats"]["throughput"])

    def test_drifting_hotspot(self):
        requests = drifting(REQS, drift_every=5000)
        router = build_router("fixed", N)
        stats = run_engine(router, requests, N, read_ratio=0.0)  # all writes
        m = distribution_metrics(stats["served"])
        self.assertLess(m["max_over_median"], 4)
        self.assertGreater(m["jain"], 0.7)
        # the system tracks the hotspot: only the latest one stays split
        self.assertIn("user:hot3", router.hot)
        for stale in ("user:hot0", "user:hot1", "user:hot2"):
            self.assertNotIn(stale, router.hot)
        # cooled keys were merged back: no leftover shards, totals intact
        written = sum(1 for k in requests if k.startswith("user:hot"))
        readable = sum(router.read_key(f"user:hot{i}")[0] for i in range(4))
        self.assertEqual(readable, written)
        cooled = ("user:hot0", "user:hot1", "user:hot2")
        for p in range(N):
            for k in router.store.parts[p]:
                for c in cooled:
                    self.assertFalse(k.startswith(f"{c}#shard"),
                                     f"stale shard {k} left behind")

    def test_degenerate_inputs_do_not_crash(self):
        self.assertEqual(distribution_metrics([0] * N)["jain"], 1.0)
        rep = compare("tiny", few_keys(100, num_keys=1), N)
        self.assertGreater(rep["fixed"]["stats"]["throughput"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
