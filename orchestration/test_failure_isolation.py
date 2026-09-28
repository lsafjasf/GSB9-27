"""Self-tests for failure isolation: propagation, skip reasons, local retry.

Run: python3 -m unittest orchestration.test_failure_isolation -v
"""

from __future__ import annotations

import unittest

from orchestration.failure_isolation import (
    DAGValidationError,
    FailureIsolationEngine,
    Status,
    Task,
    simulate_failfast,
)


def boom(message: str = "boom"):
    def _fn(_results):
        raise RuntimeError(message)

    return _fn


def ok(value: str):
    def _fn(_results):
        return value

    return _fn


def single_point_tasks():
    """7 tasks: long independent branch must survive one failure on a chain."""
    return [
        Task("pre", ok("pre")),
        Task("side1", ok("side1")),
        Task("a", ok("a"), ("pre",)),
        Task("side2", ok("side2"), ("side1",)),
        Task("a2", boom("a failed"), ("a",)),
        Task("side3", ok("side3"), ("side2",)),
        Task("a3", ok("a3"), ("a2",)),
    ]


def fanout_tasks():
    """8 tasks: a hub fans out to three children, one converges two branches."""
    return [
        Task("root", ok("root")),
        Task("hub", boom("hub failed"), ("root",)),
        Task("other1", ok("other1")),
        Task("child1", ok("child1"), ("hub",)),
        Task("other2", ok("other2"), ("other1",)),
        Task("child2", ok("child2"), ("hub",)),
        Task("other3", ok("other3"), ("other2",)),
        Task("merged", ok("merged"), ("child1", "other3")),
    ]


def shared_dependency_tasks():
    """6 tasks: diamond on a shared node plus an unrelated chain."""
    return [
        Task("shared", boom("shared failed")),
        Task("indep1", ok("indep1")),
        Task("left", ok("left"), ("shared",)),
        Task("right", ok("right"), ("shared",)),
        Task("join", ok("join"), ("left", "right")),
        Task("indep2", ok("indep2"), ("indep1",)),
    ]


def all_fail_tasks():
    """4 independent tasks, every one fails."""
    return [
        Task("f1", boom("f1 failed")),
        Task("f2", boom("f2 failed")),
        Task("f3", boom("f3 failed")),
        Task("f4", boom("f4 failed")),
    ]


class PropagationTests(unittest.TestCase):
    def test_single_point_isolates_independent_branch(self):
        engine = FailureIsolationEngine(single_point_tasks())
        report = engine.run()

        self.assertEqual(report.statuses["pre"], Status.SUCCESS)
        self.assertEqual(report.statuses["a"], Status.SUCCESS)
        self.assertEqual(report.statuses["a2"], Status.FAILED)
        self.assertEqual(report.statuses["a3"], Status.SKIPPED)
        for branch in ("side1", "side2", "side3"):
            self.assertEqual(report.statuses[branch], Status.SUCCESS)

        self.assertEqual(report.failed, 1)
        self.assertEqual(report.skipped, 1)
        self.assertEqual(report.succeeded, 5)
        self.assertEqual(report.completion_rate, 5 / 7)
        self.assertEqual(engine.explain_skip("a3"), [("a2", "a failed")])

    def test_fanout_skips_descendants_only_and_records_reasons(self):
        engine = FailureIsolationEngine(fanout_tasks())
        report = engine.run()

        self.assertEqual(report.statuses["hub"], Status.FAILED)
        for descendant in ("child1", "child2", "merged"):
            self.assertEqual(report.statuses[descendant], Status.SKIPPED)
        for survivor in ("root", "other1", "other2", "other3"):
            self.assertEqual(report.statuses[survivor], Status.SUCCESS)

        self.assertEqual((report.succeeded, report.failed, report.skipped), (4, 1, 3))
        self.assertEqual(report.completion_rate, 4 / 8)
        # child1 fails because of hub; merged is skipped because child1 was
        # skipped, but the root upstream failure reason must still be hub.
        self.assertEqual(engine.explain_skip("child1"), [("hub", "hub failed")])
        self.assertEqual(engine.explain_skip("child2"), [("hub", "hub failed")])
        self.assertEqual(engine.explain_skip("merged"), [("hub", "hub failed")])

    def test_shared_dependency_fans_skip_into_the_whole_diamond(self):
        engine = FailureIsolationEngine(shared_dependency_tasks())
        report = engine.run()

        self.assertEqual(report.statuses["shared"], Status.FAILED)
        for descendant in ("left", "right", "join"):
            self.assertEqual(report.statuses[descendant], Status.SKIPPED)
        self.assertEqual(report.statuses["indep1"], Status.SUCCESS)
        self.assertEqual(report.statuses["indep2"], Status.SUCCESS)
        self.assertEqual((report.succeeded, report.failed, report.skipped), (2, 1, 3))
        self.assertEqual(engine.impact_scope({"shared"}), {"left", "right", "join"})
        for skipped in ("left", "right", "join"):
            self.assertEqual(engine.explain_skip(skipped), [("shared", "shared failed")])

    def test_all_failures_are_counted_separately_from_skips(self):
        engine = FailureIsolationEngine(all_fail_tasks())
        report = engine.run()

        self.assertEqual({n: Status.FAILED for n in ("f1", "f2", "f3", "f4")},
                         report.statuses)
        self.assertEqual((report.succeeded, report.failed, report.skipped), (0, 4, 0))
        self.assertEqual(report.completion_rate, 0.0)
        self.assertEqual(set(report.failures), {"f1", "f2", "f3", "f4"})


class RetryTests(unittest.TestCase):
    def test_skip_records_every_failed_root_upstream(self):
        tasks = [
            Task("x", boom("x failed")),
            Task("y", boom("y failed")),
            Task("z", ok("z"), ("x", "y")),
        ]
        engine = FailureIsolationEngine(tasks)
        report = engine.run()

        self.assertEqual(report.statuses["z"], Status.SKIPPED)
        self.assertEqual(
            engine.explain_skip("z"),
            [("x", "x failed"), ("y", "y failed")],
        )

        repaired = engine.retry({"x": ok("x"), "y": ok("y")})
        self.assertEqual(repaired.statuses["z"], Status.SUCCESS)
        self.assertEqual(engine.run_count["z"], 1)

    def test_local_retry_reruns_only_affected_subgraph(self):
        engine = FailureIsolationEngine(single_point_tasks())
        engine.run()

        report = engine.retry({"a2": ok("fixed-a2")})

        self.assertEqual(report.statuses["a2"], Status.SUCCESS)
        self.assertEqual(report.statuses["a3"], Status.SUCCESS)
        for task in ("pre", "a", "side1", "side2", "side3"):
            self.assertEqual(engine.run_count[task], 1, f"{task} must run once")
        self.assertEqual(engine.run_count["a2"], 2)
        self.assertEqual(engine.run_count["a3"], 1)
        # Only the affected subgraph re-executed: a2 then a3.
        self.assertEqual(engine.execution_order[-2:], ["a2", "a3"])
        self.assertEqual(report.succeeded, 7)
        self.assertEqual(report.completion_rate, 1.0)

    def test_retry_fanout_repairs_only_failed_bubble(self):
        engine = FailureIsolationEngine(fanout_tasks())
        engine.run()
        before = dict(engine.run_count)

        report = engine.retry({"hub": ok("fixed-hub")})

        self.assertEqual(report.statuses["hub"], Status.SUCCESS)
        for task in ("child1", "child2", "merged"):
            self.assertEqual(report.statuses[task], Status.SUCCESS)
        for survivor in ("root", "other1", "other2", "other3"):
            self.assertEqual(engine.run_count[survivor], before[survivor])
        self.assertEqual(engine.run_count["hub"], before["hub"] + 1)
        # child1/child2/merged never ran during the first run.
        self.assertEqual(engine.run_count["child1"], 1)
        self.assertEqual(engine.run_count["merged"], 1)
        self.assertEqual(report.completion_rate, 1.0)

    def test_partial_fix_keeps_unrelated_failure_isolated(self):
        engine = FailureIsolationEngine(shared_dependency_tasks())
        engine.run()

        report = engine.retry({"shared": boom("still broken")})
        self.assertEqual(report.statuses["shared"], Status.FAILED)
        self.assertEqual(report.statuses["join"], Status.SKIPPED)
        self.assertEqual(engine.run_count["indep2"], 1)

        report = engine.retry({"shared": ok("fixed-shared")})
        self.assertEqual(report.statuses["join"], Status.SUCCESS)
        self.assertEqual(engine.run_count["indep1"], 1)
        self.assertEqual(engine.run_count["indep2"], 1)
        self.assertEqual(report.completion_rate, 1.0)

    def test_retry_requires_failed_task(self):
        engine = FailureIsolationEngine(single_point_tasks())
        engine.run()
        with self.assertRaises(ValueError):
            engine.retry({"a": ok("cannot rerun success")})
        with self.assertRaises(ValueError):
            engine.retry({})


class CompletionRateComparisonTests(unittest.TestCase):
    SCENARIOS = [
        ("single point", single_point_tasks, 4 / 7, 5 / 7),
        ("fanout", fanout_tasks, 2 / 8, 4 / 8),
        ("shared dependency", shared_dependency_tasks, 0 / 6, 2 / 6),
        ("all fail", all_fail_tasks, 0 / 4, 0 / 4),
    ]

    def test_isolation_never_worse_than_naive_failfast(self):
        for label, builder, baseline_rate, isolated_rate in self.SCENARIOS:
            with self.subTest(scenario=label):
                baseline = simulate_failfast(builder())
                isolated = FailureIsolationEngine(builder()).run()
                self.assertAlmostEqual(baseline.completion_rate, baseline_rate)
                self.assertAlmostEqual(isolated.completion_rate, isolated_rate)
                self.assertGreaterEqual(
                    isolated.completion_rate, baseline.completion_rate
                )

    def test_completion_rate_improves_after_local_retry(self):
        for label, builder, _baseline, isolated_rate in self.SCENARIOS[:3]:
            with self.subTest(scenario=label):
                engine = FailureIsolationEngine(builder())
                first = engine.run()
                self.assertAlmostEqual(first.completion_rate, isolated_rate)
                fixes = {
                    name: ok(f"fixed-{name}")
                    for name, status in first.statuses.items()
                    if status is Status.FAILED
                }
                repaired = engine.retry(fixes)
                self.assertAlmostEqual(repaired.completion_rate, 1.0)
                # Every successful task from the first run stayed at one run.
                for name, status in first.statuses.items():
                    if status is Status.SUCCESS:
                        self.assertEqual(engine.run_count[name], 1)


class ValidationTests(unittest.TestCase):
    def test_unknown_dependency_is_rejected(self):
        with self.assertRaises(DAGValidationError):
            FailureIsolationEngine([Task("a", ok("a"), ("ghost",))])

    def test_cycle_is_rejected(self):
        with self.assertRaises(DAGValidationError):
            FailureIsolationEngine(
                [
                    Task("a", ok("a"), ("b",)),
                    Task("b", ok("b"), ("a",)),
                ]
            )

    def test_duplicate_task_is_rejected(self):
        with self.assertRaises(DAGValidationError):
            FailureIsolationEngine([Task("a", ok("a")), Task("a", ok("a"))])


if __name__ == "__main__":
    unittest.main(verbosity=2)
