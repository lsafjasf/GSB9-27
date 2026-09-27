"""Budget-propagating timeout executor (fixed implementation).

Core idea: the timeout budget is converted *once* into an absolute
deadline (`deadline_at = start + budget`) at the top of the run, and only
that immutable deadline is propagated downwards. Steps, retries and
parallel branches can therefore never re-arm the budget:

- Seq: every child checks the same deadline; a child that times out
  aborts the sequence and the remaining children are marked CANCELLED
  without being executed (no 空转).
- Retry: all attempts (including nested retries) draw from the same
  deadline; when the deadline is reached the loop stops immediately.
- Par: all branches share the *same absolute deadline* (not a fresh copy
  of the budget). Because branches run concurrently, wall-clock cost is
  max(branch) rather than sum(branch), and each branch is individually
  capped by the deadline, so total elapsed <= remaining budget.

Time is fully injected: callers pass a clock object with now()/advance().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Status(Enum):
    OK = "ok"
    FAILED = "failed"        # retryable attempts exhausted
    TIMEOUT = "timeout"      # budget exhausted while executing
    CANCELLED = "cancelled"  # never started / aborted: budget already gone


@dataclass
class StepResult:
    name: str
    status: Status
    started_at: Optional[float]
    finished_at: Optional[float]
    attempt: int = 1


@dataclass
class RunResult:
    status: Status
    elapsed: float
    deadline_at: float
    steps: List[StepResult] = field(default_factory=list)


class FakeClock:
    """Deterministic clock injected by tests / simulations."""

    def __init__(self, start: float = 0.0):
        self._t = float(start)

    def now(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        if dt < -1e-12:
            raise ValueError("clock cannot go backwards")
        self._t += dt


# ---------------------------------------------------------------- plan nodes

@dataclass
class Work:
    """Leaf step: needs `duration` time units; the first `fail_times`
    executions end in a retryable FAILURE (still consuming the time)."""

    name: str
    duration: float
    fail_times: int = 0


@dataclass
class Seq:
    name: str
    children: list


@dataclass
class Par:
    name: str
    children: list


@dataclass
class Retry:
    name: str
    child: object
    attempts: int


# ---------------------------------------------------------------- executor

class _Deadline:
    """Immutable absolute deadline; the only thing propagated downwards."""

    __slots__ = ("at",)

    def __init__(self, at: float):
        self.at = at


def run(plan, clock, budget: float) -> RunResult:
    deadline = _Deadline(clock.now() + budget)
    start = clock.now()
    steps: List[StepResult] = []
    state: Dict[int, int] = {}
    status = _exec(plan, clock, deadline, steps, state, attempt=1)
    return RunResult(
        status=status,
        elapsed=clock.now() - start,
        deadline_at=deadline.at,
        steps=steps,
    )


def _exec(node, clock, deadline, steps, state, attempt) -> Status:
    if isinstance(node, Work):
        return _exec_work(node, clock, deadline, steps, state, attempt)
    if isinstance(node, Seq):
        return _exec_seq(node, clock, deadline, steps, state, attempt)
    if isinstance(node, Retry):
        return _exec_retry(node, clock, deadline, steps, state)
    if isinstance(node, Par):
        return _exec_par(node, clock, deadline, steps, state)
    raise TypeError(f"unknown plan node: {node!r}")


def _exec_work(node: Work, clock, deadline, steps, state, attempt) -> Status:
    if clock.now() >= deadline.at:
        # Budget already exhausted: do not even start, report CANCELLED.
        steps.append(StepResult(node.name, Status.CANCELLED, None, None, attempt))
        return Status.CANCELLED
    start = clock.now()
    remaining = deadline.at - start
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


def _exec_seq(node: Seq, clock, deadline, steps, state, attempt) -> Status:
    for index, child in enumerate(node.children):
        status = _exec(child, clock, deadline, steps, state, attempt)
        if status is Status.OK:
            continue
        if status is Status.CANCELLED:
            # Budget was already gone before this child started.
            _mark_cancelled(node.children[index + 1 :], steps)
            return Status.CANCELLED
        # TIMEOUT or FAILED: stop the sequence immediately, mark the
        # remaining children CANCELLED without executing them.
        _mark_cancelled(node.children[index + 1 :], steps)
        return status
    return Status.OK


def _mark_cancelled(children, steps) -> None:
    for child in children:
        steps.append(StepResult(child.name, Status.CANCELLED, None, None))


def _exec_retry(node: Retry, clock, deadline, steps, state) -> Status:
    last = Status.FAILED
    for k in range(1, node.attempts + 1):
        if clock.now() >= deadline.at:
            # Budget exhausted: stop retrying immediately, no busy spin.
            steps.append(StepResult(node.name, Status.CANCELLED, None, None, k))
            return Status.TIMEOUT if last is Status.TIMEOUT else Status.CANCELLED
        last = _exec(node.child, clock, deadline, steps, state, attempt=k)
        if last is Status.OK:
            return Status.OK
        if last in (Status.TIMEOUT, Status.CANCELLED):
            return last  # budget gone -> give up at once
    return last  # FAILED: attempts exhausted


def _exec_par(node: Par, clock, deadline, steps, state) -> Status:
    """All branches share the same absolute deadline. Each branch runs on
    its own lane clock (concurrent lanes); the shared clock is advanced by
    the slowest lane only, so wall-clock cost is max(lanes) <= remaining
    budget -- never sum(lanes)."""
    start = clock.now()
    lane_ends: List[float] = []
    lane_steps: List[List[StepResult]] = []
    worst = Status.OK
    for child in node.children:
        lane_clock = FakeClock(start)
        child_steps: List[StepResult] = []
        status = _exec(child, lane_clock, deadline, child_steps, state, attempt=1)
        lane_ends.append(lane_clock.now())
        lane_steps.append(child_steps)
        if status is Status.TIMEOUT:
            worst = Status.TIMEOUT
        elif status is Status.FAILED and worst is not Status.TIMEOUT:
            worst = Status.FAILED
        elif status is Status.CANCELLED and worst is Status.OK:
            worst = Status.CANCELLED
    # No lane can run past the shared deadline (Work caps consumption at
    # `remaining`), so this advance never pushes the clock beyond it.
    end = max(lane_ends, default=start)
    assert end <= deadline.at + 1e-9, "parallel lane escaped the deadline"
    clock.advance(end - start)
    for child_steps in lane_steps:
        steps.extend(child_steps)
    return worst
