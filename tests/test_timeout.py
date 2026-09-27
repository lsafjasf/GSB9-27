"""Regression tests for the shared timeout-budget engine.

All time is injected via FakeClock; durations are integer milliseconds so
every assertion is exact.
"""
import random
import unittest

from timeoutctl import (CANCELLED, FAILED, OK, TIMEOUT, Par, Retry, Seq, Step,
                        run_legacy, run_pipeline)


def by_name(report):
    return {e.path.split("/")[-1]: e for e in report.events}


class TestSingleStep(unittest.TestCase):
    def test_step_within_budget_ok(self):
        _, rep = run_pipeline(Step("a", 40), 100)
        self.assertEqual(rep.status, OK)
        self.assertEqual(rep.elapsed, 40)

    def test_step_capped_at_budget(self):
        _, rep = run_pipeline(Step("a", 500), 100)
        self.assertEqual(rep.status, TIMEOUT)
        self.assertEqual(rep.elapsed, 100)  # hard upper bound
        self.assertLessEqual(rep.elapsed, rep.budget)


class TestSerialSteps(unittest.TestCase):
    def test_serial_steps_share_one_budget(self):
        pipe = Seq("p", Step("a", 30), Step("b", 50), Step("c", 50))
        _, rep = run_pipeline(pipe, 100)
        # a: 0-30 ok, b: 30-80 ok, c: starts at 80 with only 20ms left
        st = by_name(rep)
        self.assertEqual(st["a"].status, OK)
        self.assertEqual(st["b"].status, OK)
        self.assertEqual(st["c"].status, TIMEOUT)
        self.assertEqual((st["c"].start, st["c"].end), (80, 100))
        self.assertEqual(rep.status, TIMEOUT)
        self.assertEqual(rep.elapsed, 100)  # not 130

    def test_remaining_steps_cancelled_not_started(self):
        pipe = Seq("p", Step("a", 90), Step("b", 50), Step("c", 10))
        _, rep = run_pipeline(pipe, 100)
        st = by_name(rep)
        self.assertEqual(st["b"].status, TIMEOUT)          # ran, hit deadline
        self.assertEqual(st["c"].status, CANCELLED)        # never started
        self.assertIsNone(st["c"].start)
        self.assertIsNone(st["c"].end)
        self.assertEqual(rep.elapsed, 100)

    def test_timeout_and_cancelled_are_distinguishable(self):
        pipe = Seq("p", Step("a", 90), Step("b", 50), Step("c", 10))
        _, rep = run_pipeline(pipe, 100)
        statuses = {name: e.status for name, e in by_name(rep).items()}
        self.assertEqual(rep.status, TIMEOUT)
        self.assertEqual(statuses["b"], TIMEOUT)
        self.assertEqual(statuses["c"], CANCELLED)
        self.assertNotEqual(TIMEOUT, CANCELLED)

    def test_no_busy_spin_after_budget_exhausted(self):
        pipe = Seq("p", Step("a", 1000), Step("b", 1), Step("c", 1), Step("d", 1))
        _, rep = run_pipeline(pipe, 100)
        self.assertEqual(rep.elapsed, 100)
        started = [e.path for e in rep.events
                   if e.start is not None and "/" in e.path]  # leaf steps only
        self.assertEqual(started, ["p/a"])  # only "a" ever ran
        for e in rep.events:
            if e.end is not None:
                self.assertLessEqual(e.end, 100)


class TestRetry(unittest.TestCase):
    def test_retry_draws_from_same_budget(self):
        pipe = Seq("p", Step("a", 50),
                   Retry("r", Step("b", 60, fail_times=99), attempts=5))
        _, rep = run_pipeline(pipe, 120)
        # a: 0-50 ok. attempt1: 50-110 fails. attempt2: only 10ms left ->
        # capped, times out at 120. Attempts 3-5 never start.
        b_events = [e for e in rep.events if e.path.endswith("/b")]
        self.assertEqual(len(b_events), 2)
        self.assertEqual([e.status for e in b_events], [FAILED, TIMEOUT])
        self.assertEqual(b_events[1].end, 120)
        self.assertEqual(rep.status, TIMEOUT)
        self.assertEqual(rep.elapsed, 120)  # not 50 + 5*60 = 350

    def test_retry_success_within_budget(self):
        pipe = Retry("r", Step("b", 30, fail_times=2), attempts=3)
        _, rep = run_pipeline(pipe, 100)
        self.assertEqual(rep.status, OK)
        self.assertEqual(rep.elapsed, 90)  # 3 attempts share the budget

    def test_nested_retries_share_one_budget(self):
        pipe = Retry("outer",
                     Retry("inner", Step("s", 40, fail_times=99), attempts=3),
                     attempts=3)
        _, rep = run_pipeline(pipe, 100)
        # inner: 0-40 fail, 40-80 fail, 80-100 capped -> timeout.
        # outer stops immediately; 3 attempts total, not 9.
        s_events = [e for e in rep.events if e.path.endswith("/s")]
        self.assertEqual([e.status for e in s_events], [FAILED, FAILED, TIMEOUT])
        self.assertEqual(rep.status, TIMEOUT)
        self.assertEqual(rep.elapsed, 100)  # not 9*40 = 360


class TestParallel(unittest.TestCase):
    def test_parallel_wall_time_is_max_not_sum(self):
        pipe = Par("par", Step("a", 90), Step("b", 70), Step("c", 50))
        _, rep = run_pipeline(pipe, 100)
        self.assertEqual(rep.status, OK)
        self.assertEqual(rep.elapsed, 90)  # max, not 210

    def test_parallel_branches_share_one_deadline(self):
        pipe = Seq("p", Step("a", 40), Par("par", Step("b", 90), Step("c", 90)))
        _, rep = run_pipeline(pipe, 100)
        # Both branches see remaining=60ms and are capped at the SAME
        # deadline (t=100): each gets the remaining budget, not a fresh 100.
        st = by_name(rep)
        self.assertEqual((st["b"].start, st["b"].end), (40, 100))
        self.assertEqual((st["c"].start, st["c"].end), (40, 100))
        self.assertEqual(st["b"].status, TIMEOUT)
        self.assertEqual(st["c"].status, TIMEOUT)
        self.assertEqual(rep.elapsed, 100)  # assertable upper bound

    def test_parallel_partial_timeout(self):
        pipe = Par("par", Step("a", 150), Step("b", 30))
        _, rep = run_pipeline(pipe, 100)
        st = by_name(rep)
        self.assertEqual(st["a"].status, TIMEOUT)
        self.assertEqual(st["b"].status, OK)
        self.assertEqual(rep.status, TIMEOUT)
        self.assertEqual(rep.elapsed, 100)


class TestLegacyBug(unittest.TestCase):
    @staticmethod
    def build():
        return Seq("pipeline",
                   Step("fetch", 80),
                   Retry("retry", Step("flaky", 60, fail_times=2), attempts=3),
                   Par("fanout", Step("slow", 90), Step("fast", 70)))

    def test_legacy_reproduces_the_bug(self):
        _, rep = run_legacy(self.build(), 100)
        self.assertEqual(rep.status, OK)          # every step "succeeded"...
        self.assertEqual(rep.elapsed, 350)        # ...but 3.5x over budget
        self.assertGreater(rep.elapsed, rep.budget)

    def test_fixed_engine_same_pipeline(self):
        _, rep = run_pipeline(self.build(), 100)
        self.assertEqual(rep.status, TIMEOUT)
        self.assertEqual(rep.elapsed, 100)
        self.assertLessEqual(rep.elapsed, rep.budget)


# ------------------------------------------------------------ random pipelines

def random_pipeline(rng, depth=0, _counter=[0]):
    if depth >= 3 or rng.random() < 0.45:
        _counter[0] += 1
        return Step("s%d" % _counter[0], rng.randint(10, 120),
                    fail_times=rng.choice([0, 0, 0, 1, 2]))
    _counter[0] += 1
    name = "n%d" % _counter[0]
    kind = rng.choice(["seq", "retry", "par"])
    if kind == "seq":
        return Seq(name, *[random_pipeline(rng, depth + 1)
                           for _ in range(rng.randint(2, 4))])
    if kind == "retry":
        return Retry(name, random_pipeline(rng, depth + 1), rng.randint(2, 3))
    return Par(name, *[random_pipeline(rng, depth + 1)
                       for _ in range(rng.randint(2, 3))])


class TestBudgetUpperBoundProperty(unittest.TestCase):
    def test_randomized_pipelines_never_exceed_budget(self):
        rng = random.Random(20260928)
        for _ in range(300):
            pipe = random_pipeline(rng)
            budget = rng.choice([50, 100, 200])
            _, rep = run_pipeline(pipe, budget)
            self.assertLessEqual(rep.elapsed, rep.budget,
                                 "elapsed %s > budget %s" % (rep.elapsed, budget))
            for e in rep.events:
                if e.end is not None:
                    self.assertLessEqual(e.end, budget)


if __name__ == "__main__":
    unittest.main()
