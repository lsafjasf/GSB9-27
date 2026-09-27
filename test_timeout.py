"""Regression tests for the budget-propagating timeout executor.

Time is injected via FakeClock; no real sleeping happens.
"""
import unittest

from budget_timeout import (
    FakeClock,
    Par,
    Retry,
    Seq,
    Status,
    Work,
    run,
)
from legacy_timeout import run as legacy_run

EPS = 1e-9


def statuses(result):
    return [s.status for s in result.steps]


class SingleStepTests(unittest.TestCase):
    def test_ok_within_budget(self):
        result = run(Work("a", 0.4), FakeClock(), budget=1.0)
        self.assertEqual(result.status, Status.OK)
        self.assertAlmostEqual(result.elapsed, 0.4)

    def test_timeout_when_step_exceeds_budget(self):
        result = run(Work("a", 1.5), FakeClock(), budget=1.0)
        self.assertEqual(result.status, Status.TIMEOUT)
        self.assertAlmostEqual(result.elapsed, 1.0)  # capped at the budget
        self.assertEqual(statuses(result), [Status.TIMEOUT])

    def test_zero_budget_cancels_immediately(self):
        result = run(Work("a", 0.1), FakeClock(), budget=0.0)
        self.assertEqual(result.status, Status.CANCELLED)
        self.assertAlmostEqual(result.elapsed, 0.0)
        self.assertEqual(statuses(result), [Status.CANCELLED])


class SerialTests(unittest.TestCase):
    def test_steps_share_one_budget(self):
        plan = Seq("pipe", [Work("s1", 0.6), Work("s2", 0.6), Work("s3", 0.6)])
        result = run(plan, FakeClock(), budget=1.0)
        self.assertEqual(result.status, Status.TIMEOUT)
        self.assertLessEqual(result.elapsed, 1.0 + EPS)
        # s1 ok (0.6), s2 times out after consuming the remaining 0.4,
        # s3 is cancelled without running.
        self.assertEqual(
            statuses(result), [Status.OK, Status.TIMEOUT, Status.CANCELLED]
        )
        self.assertIsNone(result.steps[2].started_at)

    def test_no_overshoot_with_many_steps(self):
        plan = Seq("pipe", [Work(f"s{i}", 0.6) for i in range(5)])
        result = run(plan, FakeClock(), budget=1.0)
        self.assertAlmostEqual(result.elapsed, 1.0)
        self.assertLessEqual(result.elapsed, 1.0 + EPS)

    def test_all_fit_within_budget(self):
        plan = Seq("pipe", [Work("s1", 0.3), Work("s2", 0.3)])
        result = run(plan, FakeClock(), budget=1.0)
        self.assertEqual(result.status, Status.OK)
        self.assertAlmostEqual(result.elapsed, 0.6)


class RetryTests(unittest.TestCase):
    def test_retry_draws_from_same_budget(self):
        # Each attempt costs 0.6 and fails; budget 1.0 fits exactly one
        # full attempt plus 0.4 of the second. No third attempt may start.
        plan = Retry("flaky", Work("call", 0.6, fail_times=99), attempts=3)
        result = run(plan, FakeClock(), budget=1.0)
        self.assertEqual(result.status, Status.TIMEOUT)
        self.assertAlmostEqual(result.elapsed, 1.0)
        attempts = [s.attempt for s in result.steps]
        self.assertEqual(attempts, [1, 2])  # attempt 3 never started

    def test_nested_retries_share_one_budget(self):
        plan = Retry(
            "outer",
            Retry("inner", Work("call", 0.6, fail_times=99), attempts=2),
            attempts=2,
        )
        result = run(plan, FakeClock(), budget=1.0)
        self.assertEqual(result.status, Status.TIMEOUT)
        self.assertAlmostEqual(result.elapsed, 1.0)
        work_attempts = [s for s in result.steps if s.name == "call"]
        self.assertEqual(len(work_attempts), 2)  # not 2x2=4

    def test_retry_eventually_succeeds(self):
        plan = Retry("flaky", Work("call", 0.3, fail_times=2), attempts=3)
        result = run(plan, FakeClock(), budget=1.0)
        self.assertEqual(result.status, Status.OK)
        self.assertAlmostEqual(result.elapsed, 0.9)

    def test_attempts_exhausted_is_failed_not_timeout(self):
        plan = Retry("flaky", Work("call", 0.1, fail_times=99), attempts=3)
        result = run(plan, FakeClock(), budget=1.0)
        self.assertEqual(result.status, Status.FAILED)
        self.assertAlmostEqual(result.elapsed, 0.3)


class ParallelTests(unittest.TestCase):
    def test_parallel_wall_time_is_max_not_sum(self):
        plan = Par("fan", [Work("a", 0.3), Work("b", 0.5), Work("c", 0.4)])
        result = run(plan, FakeClock(), budget=1.0)
        self.assertEqual(result.status, Status.OK)
        self.assertAlmostEqual(result.elapsed, 0.5)  # max, not 1.2

    def test_parallel_total_never_exceeds_budget(self):
        # Each branch alone would take 1.2; concurrently they must still
        # be capped by the shared deadline at 1.0.
        plan = Par(
            "fan",
            [Seq(f"lane{i}", [Work(f"l{i}a", 0.6), Work(f"l{i}b", 0.6)])
             for i in range(3)],
        )
        result = run(plan, FakeClock(), budget=1.0)
        self.assertEqual(result.status, Status.TIMEOUT)
        self.assertLessEqual(result.elapsed, 1.0 + EPS)  # assertable upper bound
        self.assertAlmostEqual(result.elapsed, 1.0)
        # Every lane: first step OK, second step TIMEOUT.
        self.assertEqual(
            statuses(result),
            [Status.OK, Status.TIMEOUT] * 3,
        )

    def test_parallel_then_serial_tail_is_cancelled(self):
        plan = Seq(
            "pipe",
            [
                Par("fan", [Work("a", 0.8), Work("b", 0.9)]),
                Work("tail", 0.5),
            ],
        )
        result = run(plan, FakeClock(), budget=1.0)
        self.assertEqual(result.status, Status.TIMEOUT)
        self.assertLessEqual(result.elapsed, 1.0 + EPS)
        self.assertEqual(result.steps[-1].name, "tail")
        self.assertEqual(result.steps[-1].status, Status.TIMEOUT)
        # tail started at 0.9 with only 0.1 left
        self.assertAlmostEqual(result.steps[-1].started_at, 0.9)

    def test_parallel_after_budget_exhausted_cancels_all_lanes(self):
        plan = Seq(
            "pipe",
            [Work("first", 1.0), Par("fan", [Work("a", 0.1), Work("b", 0.1)])],
        )
        result = run(plan, FakeClock(), budget=1.0)
        self.assertEqual(result.status, Status.CANCELLED)
        self.assertAlmostEqual(result.elapsed, 1.0)
        self.assertEqual(
            statuses(result), [Status.OK, Status.CANCELLED, Status.CANCELLED]
        )


class RegressionAgainstLegacyTests(unittest.TestCase):
    """Documents the bug: the legacy executor violates the budget on the
    same plans; the fixed executor does not."""

    PLANS = {
        "serial": Seq("p", [Work(f"s{i}", 0.6) for i in range(5)]),
        "retry": Retry("r", Work("c", 0.6, fail_times=99), attempts=3),
        "parallel": Par("f", [Work(f"b{i}", 0.6) for i in range(3)]),
    }

    def test_legacy_violates_budget(self):
        for name, plan in self.PLANS.items():
            with self.subTest(name=name):
                legacy = legacy_run(plan, FakeClock(), budget=1.0)
                self.assertGreater(legacy.elapsed, 1.0 + EPS)

    def test_fixed_respects_budget(self):
        for name, plan in self.PLANS.items():
            with self.subTest(name=name):
                fixed = run(plan, FakeClock(), budget=1.0)
                self.assertLessEqual(fixed.elapsed, 1.0 + EPS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
