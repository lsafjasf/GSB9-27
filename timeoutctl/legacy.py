"""The legacy buggy behaviour, kept for reproduction and regression tests.

Bug: every step, every retry attempt and every parallel branch receives the
FULL timeout as a brand-new budget. Each step individually stays under the
limit, but serial steps, retries and branches add up, so the end-to-end
latency sails past the agreed budget.
"""
from .clock import FakeClock
from .engine import Engine, FreshBudgetPerStep


def run_legacy(node, timeout, clock=None):
    clock = clock or FakeClock()
    report = Engine(clock).run(node, timeout, policy=FreshBudgetPerStep(timeout))
    return clock, report
