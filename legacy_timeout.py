"""Legacy (buggy) timeout executor -- kept as the "before" baseline for the repro.

Bug: every step computes its own fresh deadline from the *full* budget
(`now + budget`), retries reset the budget per attempt, and parallel
branches each receive the full budget and are charged sequentially.
Nothing propagates top-down, so the total elapsed time of a multi-step
task can be many times the agreed budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from budget_timeout import Par, Retry, Seq, Status, StepResult, Work


@dataclass
class LegacyRunResult:
    status: Status
    elapsed: float
    steps: List[StepResult] = field(default_factory=list)


def run(plan, clock, budget: float) -> LegacyRunResult:
    start = clock.now()
    steps: List[StepResult] = []
    state = {}
    status = _exec(plan, clock, budget, steps, state, attempt=1)
    return LegacyRunResult(status=status, elapsed=clock.now() - start, steps=steps)


def _exec(node, clock, budget, steps, state, attempt) -> Status:
    if isinstance(node, Work):
        return _exec_work(node, clock, budget, steps, state, attempt)
    if isinstance(node, Seq):
        worst = Status.OK
        for child in node.children:
            # BUG: each child gets the full budget again.
            st = _exec(child, clock, budget, steps, state, attempt)
            # BUG: a timed-out step does not stop the sequence; the
            # remaining steps keep burning time ("空转").
            if st is Status.TIMEOUT:
                worst = Status.TIMEOUT
            elif st is Status.FAILED and worst is Status.OK:
                worst = Status.FAILED
        return worst
    if isinstance(node, Retry):
        last = Status.FAILED
        for k in range(1, node.attempts + 1):
            # BUG: every attempt re-arms the full budget.
            last = _exec(node.child, clock, budget, steps, state, attempt=k)
            if last is Status.OK:
                return Status.OK
        return last
    if isinstance(node, Par):
        worst = Status.OK
        for child in node.children:
            # BUG: branches are charged sequentially on the shared clock,
            # each with its own fresh full budget.
            st = _exec(child, clock, budget, steps, state, attempt)
            if st is Status.TIMEOUT:
                worst = Status.TIMEOUT
        return worst
    raise TypeError(f"unknown plan node: {node!r}")


def _exec_work(node: Work, clock, budget, steps, state, attempt) -> Status:
    start = clock.now()
    # BUG: deadline is re-computed from the full budget at every step.
    deadline_at = start + budget
    remaining = deadline_at - start
    consumed = min(node.duration, remaining)
    clock.advance(consumed)
    if node.duration > remaining:
        steps.append(StepResult(node.name, Status.TIMEOUT, start, clock.now(), attempt))
        return Status.TIMEOUT
    failures_left = state.setdefault(id(node), node.fail_times)
    if failures_left > 0:
        state[id(node)] = failures_left - 1
        steps.append(StepResult(node.name, Status.FAILED, start, clock.now(), attempt))
        return Status.FAILED
    steps.append(StepResult(node.name, Status.OK, start, clock.now(), attempt))
    return Status.OK
