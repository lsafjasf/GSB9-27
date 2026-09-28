"""Timeout-budget engine (the fixed implementation).

One Budget is created at the root and propagates top-down:

* Seq    - each child receives the SAME budget (only the remainder is left).
* Retry  - every attempt draws from the SAME budget; attempts never reset it.
* Par    - all branches share the SAME deadline (see note below).

The difference between the fixed engine and the legacy buggy one is a single
"policy" object deciding which budget a child gets:

* SharedBudgetPolicy   (the fix):  child gets the parent's budget object.
* FreshBudgetPerStep   (the bug):  child gets a brand-new full budget.

Parallel budget allocation
--------------------------
Branches are NOT given budget/2, budget/3, ... slices. They run concurrently,
so the wall-time cost of a Par node is max(branch durations), not the sum.
Giving every branch the same remaining budget (one shared deadline) is both
correct and tight: elapsed(Par) <= remaining, hence the whole pipeline stays
within budget. Sub-dividing the budget would only make sense to *reserve*
budget for later serial steps; that is available explicitly via
`Budget.limited()` but is not needed for the bound to hold.
"""
import heapq
from dataclasses import dataclass

from .budget import Budget
from .clock import FakeClock
from .tasks import Par, Retry, Seq, Step

OK = "ok"
TIMEOUT = "timeout"
CANCELLED = "cancelled"
FAILED = "failed"


@dataclass
class Event:
    path: str
    status: str
    start: float  # None => never started (cancelled before running)
    end: float


@dataclass
class RunReport:
    status: str
    elapsed: float
    budget: float
    events: list

    @property
    def within_budget(self):
        return self.elapsed <= self.budget

    def events_by(self, status):
        return [e for e in self.events if e.status == status]


# ---------------------------------------------------------------- budget policy

class SharedBudgetPolicy:
    """THE FIX: child steps, retry attempts and parallel branches all draw
    from the SAME budget (one shared deadline). Nothing resets it."""

    def child(self, budget, clock):
        return budget


SHARED_BUDGET = SharedBudgetPolicy()


class FreshBudgetPerStep:
    """THE ORIGINAL BUG: hands a brand-new full budget to every child step,
    every retry attempt and every parallel branch. Each individual step stays
    under `timeout`, but the totals add up and the pipeline blows past the
    agreed limit. Kept only to reproduce the bug and to regression-test."""

    def __init__(self, timeout):
        self.timeout = timeout

    def child(self, budget, clock):
        return Budget.fresh(clock, self.timeout)


# ------------------------------------------------------------------ evaluation

class _Sleep:
    __slots__ = ("duration",)

    def __init__(self, duration):
        self.duration = duration


class _Fork:
    __slots__ = ("gens",)

    def __init__(self, gens):
        self.gens = gens


def _child_path(path, name, fallback):
    return "%s/%s" % (path, name or fallback)


class _Ctx:
    def __init__(self, clock):
        self.clock = clock
        self.events = []
        # Per-run failure counters, keyed by Step identity. A fresh _Ctx is
        # created for every run, so each run gets independent statistics
        # state and re-running the same plan cannot leak attempts across
        # runs (the Step spec objects stay immutable).
        self._fail_counts = {}

    def should_fail(self, step):
        """True while this run has not yet used up step.fail_times."""
        used = self._fail_counts.get(id(step), 0)
        if used < step.fail_times:
            self._fail_counts[id(step)] = used + 1
            return True
        return False

    def record(self, path, status, start, end):
        self.events.append(Event(path, status, start, end))

    def record_cancelled_subtree(self, node, path):
        """Mark a never-started subtree as cancelled (no time consumed)."""
        self.record(path, CANCELLED, None, None)
        if isinstance(node, Seq):
            for i, c in enumerate(node.children):
                self.record_cancelled_subtree(c, _child_path(path, c.name, "#%d" % i))
        elif isinstance(node, Par):
            for i, b in enumerate(node.branches):
                self.record_cancelled_subtree(b, _child_path(path, b.name, "#%d" % i))
        elif isinstance(node, Retry):
            self.record_cancelled_subtree(node.child, "%s/%s" % (path, node.child.name))


def evaluate(node, budget, ctx, path, policy):
    """Generator: yields _Sleep/_Fork to the scheduler, returns a status."""
    clock = ctx.clock
    if budget.exhausted(clock):
        # Budget already gone: do not even start, report as cancelled.
        ctx.record_cancelled_subtree(node, path)
        return CANCELLED

    if isinstance(node, Step):
        start = clock.now()
        # The step may only consume what is left of the budget.
        run_for = min(node.duration, budget.remaining(clock))
        if run_for > 0:
            yield _Sleep(run_for)
        end = clock.now()
        if run_for < node.duration:
            ctx.record(path, TIMEOUT, start, end)
            return TIMEOUT
        if ctx.should_fail(node):
            ctx.record(path, FAILED, start, end)
            return FAILED
        ctx.record(path, OK, start, end)
        return OK

    if isinstance(node, Seq):
        t0 = clock.now()
        for i, child in enumerate(node.children):
            cpath = _child_path(path, child.name, "#%d" % i)
            status = yield from evaluate(
                child, policy.child(budget, clock), ctx, cpath, policy)
            if status != OK:
                for j in range(i + 1, len(node.children)):
                    rest = node.children[j]
                    ctx.record_cancelled_subtree(
                        rest, _child_path(path, rest.name, "#%d" % j))
                final = TIMEOUT if status == CANCELLED else status
                ctx.record(path, final, t0, clock.now())
                return final
        ctx.record(path, OK, t0, clock.now())
        return OK

    if isinstance(node, Retry):
        t0 = clock.now()
        for attempt in range(1, node.attempts + 1):
            cpath = "%s/attempt[%d]/%s" % (path, attempt, node.child.name)
            # Retry attempts draw from the SAME budget (fix) or a fresh one (bug).
            status = yield from evaluate(
                node.child, policy.child(budget, clock), ctx, cpath, policy)
            if status == OK:
                ctx.record(path, OK, t0, clock.now())
                return OK
            if status in (TIMEOUT, CANCELLED):
                # Budget is gone: stop immediately, do not spin more attempts.
                ctx.record(path, TIMEOUT, t0, clock.now())
                return TIMEOUT
        ctx.record(path, FAILED, t0, clock.now())
        return FAILED

    if isinstance(node, Par):
        t0 = clock.now()
        gens = [
            evaluate(b, policy.child(budget, clock), ctx,
                     _child_path(path, b.name, "#%d" % i), policy)
            for i, b in enumerate(node.branches)
        ]
        results = yield _Fork(gens)
        if any(r in (TIMEOUT, CANCELLED) for r in results):
            final = TIMEOUT
        elif any(r == FAILED for r in results):
            final = FAILED
        else:
            final = OK
        ctx.record(path, final, t0, clock.now())
        return final

    raise TypeError("unknown node %r" % (node,))


# ------------------------------------------------------------------ scheduler

class _Fiber:
    __slots__ = ("gen", "done", "result", "parent", "children")

    def __init__(self, gen):
        self.gen = gen
        self.done = False
        self.result = None
        self.parent = None
        self.children = ()


class Engine:
    """Discrete-event scheduler. Drives task-tree fibers on the injected
    clock; parallel branches interleave on the same virtual timeline."""

    def __init__(self, clock):
        self.clock = clock
        self._seq = 0

    def run(self, node, total_budget, policy=None):
        policy = policy or SHARED_BUDGET
        clock = self.clock
        start = clock.now()
        budget = Budget.fresh(clock, total_budget)
        ctx = _Ctx(clock)
        root = _Fiber(evaluate(node, budget, ctx, node.name or "root", policy))
        pending = []  # heap of (wake_at, seq, fiber)
        self._drive(root, None, pending)
        while pending:
            wake_at, _, fiber = heapq.heappop(pending)
            if fiber.done:
                continue
            dt = wake_at - clock.now()
            if dt > 0:
                clock.advance(dt)
            self._drive(fiber, None, pending)
        assert root.done, "scheduler finished with the root fiber suspended"
        return RunReport(status=root.result,
                         elapsed=clock.now() - start,
                         budget=total_budget,
                         events=ctx.events)

    def _drive(self, fiber, value, pending):
        while True:
            try:
                instr = fiber.gen.send(value)
            except StopIteration as stop:
                fiber.done = True
                fiber.result = stop.value
                self._completed(fiber, pending)
                return
            if isinstance(instr, _Sleep):
                self._seq += 1
                heapq.heappush(
                    pending, (self.clock.now() + instr.duration, self._seq, fiber))
                return
            if isinstance(instr, _Fork):
                children = []
                for g in instr.gens:
                    c = _Fiber(g)
                    c.parent = fiber
                    children.append(c)
                fiber.children = children
                for c in children:
                    self._drive(c, None, pending)
                return
            raise TypeError("unknown instruction %r" % (instr,))

    def _completed(self, fiber, pending):
        parent = fiber.parent
        if parent is not None and all(c.done for c in parent.children):
            self._drive(parent, [c.result for c in parent.children], pending)


def run_pipeline(node, budget, clock=None):
    """Run a task tree with the FIXED shared-budget semantics."""
    clock = clock or FakeClock()
    report = Engine(clock).run(node, budget)
    return clock, report
