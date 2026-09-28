"""Self tests for cost-aware sharding, exact progress and retry/re-split."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shardlib import (
    Executor,
    Failure,
    Item,
    Shard,
    child_shard_count,
    distribution,
    split_cost_balanced,
    split_even,
    split_quality,
)
from shardlib.progress import ProgressTracker


def make_items(spec):
    """spec: list of (id, cost[, duration]) -> Items with duration in payload."""
    return [
        Item(item_id=i, cost=cost, payload=dur)
        for i, cost, *rest in spec
        for dur in (rest[0] if rest else cost,)
    ]


def duration_of(item):
    return item.payload if item.payload is not None else item.cost


class FixedHandler:
    """Deterministic handler: succeeds after ``ok_at`` attempts, else Failure."""

    def __init__(self, ok_after=None, fail_ids=(), tick_overhead=0.0):
        self.ok_after = dict(ok_after or {})
        self.fail_ids = set(fail_ids)
        self.overhead = tick_overhead

    def __call__(self, item, attempt):
        dur = duration_of(item)
        if item.item_id in self.fail_ids or attempt <= self.ok_after.get(item.item_id, 0):
            return Failure(error=f"boom@{attempt}", spent_work=dur)
        return float(dur) + self.overhead


class SplitterTests(unittest.TestCase):
    def test_balanced_cut_beats_even_cut_on_skewed_costs(self):
        # Zipf-like skew: fixed-count chunks put most heavy items together.
        costs = [100, 90, 80, 1, 1, 1, 1, 1, 1, 1, 1, 1]
        items = [Item(f"i{idx}", cost=cost) for idx, cost in enumerate(costs)]
        balanced = split_cost_balanced(items, 3)
        even = split_even(items, 3)
        qb, qe = split_quality(balanced), split_quality(even)
        self.assertEqual(sum(len(s.items) for s in balanced), len(items))
        self.assertLess(qb["max_load"], qe["max_load"])
        self.assertGreater(qb["utilisation_lower_bound"], qe["utilisation_lower_bound"])
        # LPT guarantee: no shard exceeds the 4/3 - 1/9 bound over the LB.
        lb = sum(costs) / 3
        self.assertLessEqual(qb["max_load"] / lb, 4 / 3 - 1 / 9 + 1e-9)
        # Perfectly even costs -> perfectly even cut.
        flat = [Item(f"f{i}", cost=5) for i in range(12)]
        q_flat = split_quality(split_cost_balanced(flat, 4))
        self.assertAlmostEqual(q_flat["imbalance_ratio"], 0.0)

    def test_shard_count_greater_than_task_count(self):
        items = [Item("a", 3), Item("b", 1)]
        shards = split_cost_balanced(items, 10)
        self.assertEqual(len(shards), 2)  # no empty shards invented
        self.assertEqual(sum(len(s.items) for s in shards), 2)
        q = split_quality(shards, total_cost=4.0)
        self.assertEqual(q["shards_nonempty"], 2.0)

    def test_single_extremely_long_item(self):
        items = [Item("giant", 1000)] + [Item(f"x{i}", 1) for i in range(20)]
        shards = split_cost_balanced(items, 4)
        loads = sorted(s.estimated_cost for s in shards)
        # The giant gets its own shard and never shares; small items spread.
        self.assertIn(1000.0, loads)
        self.assertTrue(all(load <= 1000.0 for load in loads))

    def test_child_shard_count_shrinks_with_depth(self):
        self.assertEqual(child_shard_count(100, 1, 8), 4)
        self.assertEqual(child_shard_count(100, 2, 8), 2)
        self.assertEqual(child_shard_count(100, 5, 8), 1)
        self.assertEqual(child_shard_count(1, 1, 8), 1)


class ProgressTests(unittest.TestCase):
    def test_progress_is_work_ratio_not_shard_ratio(self):
        # Shard A: 99% of the work; shard B: 1%. After B finishes, a shard
        # counter would say 50%; work-based progress must say 1%.
        tracker = ProgressTracker([Item("A", 99.0), Item("B", 1.0)])
        tracker.mark_completed("B")
        self.assertAlmostEqual(tracker.fraction(), 0.01)
        self.assertNotAlmostEqual(tracker.fraction(), 0.5)
        tracker.assert_consistent(["B"])
        tracker.mark_completed("A")
        self.assertAlmostEqual(tracker.fraction(), 1.0)
        tracker.assert_consistent(["B", "A"])

    def test_double_commit_is_rejected(self):
        tracker = ProgressTracker([Item("A", 1.0)])
        tracker.mark_completed("A")
        with self.assertRaises(AssertionError):
            tracker.mark_completed("A")

    def test_dynamic_addition_keeps_fraction_exact(self):
        tracker = ProgressTracker([Item("a", 50.0)])
        tracker.mark_completed("a")
        self.assertAlmostEqual(tracker.fraction(), 1.0)
        tracker.add_items([Item("b", 50.0)])
        # Total work doubled, completed stayed -> exactly 0.5, no 100% lie.
        self.assertAlmostEqual(tracker.fraction(), 0.5)
        tracker.assert_consistent(["a"])


class ExecutorTests(unittest.TestCase):
    def test_happy_run_progress_snapshots_exact_and_monotone(self):
        items = make_items([("a", 40, 40), ("b", 30, 30), ("c", 20, 20), ("d", 10, 10)])
        result = Executor(FixedHandler(), shard_count=2, workers=2).run(items)
        self.assertTrue(result.ok)
        self.assertEqual(result.succeeded, ["a", "b", "c", "d"])
        self.assertEqual(set(result.invocations), {"a", "b", "c", "d"})
        self.assertTrue(all(n == 1 for n in result.invocations.values()))
        # Every snapshot equals completed_work/total_work and never decreases.
        fracs = [s.fraction for s in result.snapshots]
        for prev, nxt in zip(fracs, fracs[1:]):
            self.assertGreaterEqual(nxt + 1e-12, prev)
        for snap in result.snapshots:
            if snap.total_work:
                self.assertAlmostEqual(
                    snap.fraction, snap.completed_work / snap.total_work, places=12
                )
        self.assertAlmostEqual(result.final_fraction, 1.0)

    def test_all_items_fail(self):
        items = make_items([("a", 5), ("b", 5), ("c", 5)])
        result = Executor(
            FixedHandler(fail_ids={"a", "b", "c"}),
            shard_count=2,
            workers=2,
            max_item_attempts=3,
        ).run(items)
        self.assertFalse(result.ok)
        self.assertEqual(set(result.dead), {"a", "b", "c"})
        self.assertEqual(result.succeeded, [])
        # Each item attempted exactly max_item_attempts times, then stops.
        self.assertTrue(all(n == 3 for n in result.invocations.values()))
        # Progress stays at zero even though many shards ran and re-split.
        self.assertAlmostEqual(result.final_fraction, 0.0)
        self.assertTrue(result.resplits)
        self.assertGreater(result.wasted_work, 0.0)

    def test_failed_shard_resplits_without_reprocessing_done_items(self):
        # ``a`` fails twice then succeeds; b succeeds first try.
        # Simulate a shard [a, b]: b commits during the first run, and only a
        # may be retried in child shards.
        items = make_items([("a", 10, 10), ("b", 10, 10)])
        result = Executor(
            FixedHandler(ok_after={"a": 2}),
            shard_count=1,
            workers=1,
            max_item_attempts=4,
        ).run(items)
        self.assertTrue(result.ok)
        self.assertEqual(result.invocations["b"], 1)  # b never reprocessed
        self.assertEqual(result.invocations["a"], 3)
        # The resplit children contain only the residual item.
        self.assertEqual(len(result.resplits), 2)
        self.assertEqual(result.resplits[0]["residual_items"], ["a"])
        self.assertEqual(result.resplits[1]["residual_items"], ["a"])
        # Snapshot right after b's first success must be 50% (work), not 0%.
        mid = next(s for s in result.snapshots if s.completed_items == 1)
        self.assertAlmostEqual(mid.fraction, 0.5)

    def test_dead_items_are_skipped_not_reprocessed(self):
        items = make_items([("a", 2), ("b", 2)])
        result = Executor(
            FixedHandler(fail_ids={"a"}),
            shard_count=1,
            workers=1,
            max_item_attempts=2,
        ).run(items)
        self.assertEqual(result.invocations["a"], 2)
        self.assertEqual(result.invocations["b"], 1)
        self.assertEqual(result.dead, ["a"])
        self.assertEqual(result.succeeded, ["b"])

    def test_dynamic_new_items_extend_total_work(self):
        initial = [Item("a", 10.0, 10.0)]
        late = [(5.0, [Item("b", 30.0, 30.0)])]
        result = Executor(FixedHandler(), shard_count=1, workers=1).run(
            initial, late_items=late
        )
        self.assertEqual(result.succeeded, ["a", "b"])
        self.assertEqual(result.added_item_ids, ["b"])
        # After a finishes the bar must account for b and be 25%.
        snap25 = next(s for s in result.snapshots if s.completed_items == 1)
        self.assertAlmostEqual(snap25.completed_work, 10.0)
        self.assertAlmostEqual(snap25.total_work, 40.0)
        self.assertAlmostEqual(snap25.fraction, 0.25)
        self.assertAlmostEqual(result.final_fraction, 1.0)

    def test_metrics_distribution_helper(self):
        d = distribution([1, 2, 3, 4])
        self.assertEqual(d["min"], 1)
        self.assertEqual(d["max"], 4)
        self.assertAlmostEqual(d["mean"], 2.5)
        self.assertAlmostEqual(d["p50"], 2.5)
        self.assertGreaterEqual(d["p90"], 3.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
