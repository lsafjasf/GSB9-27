"""Failure isolation for a small DAG task orchestrator (stdlib only).

Core guarantees:

1. A failing task is marked FAILED; every task that transitively depends on a
   failed task is marked SKIPPED with the root upstream failure(s) recorded.
2. Independent branches keep running, so the whole orchestration never stops
   just because one unrelated subtask failed.
3. Retry is local: only the failed task and the subgraph depending on it are
   reset and re-executed. Tasks that already SUCCEEDED are never re-run.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


TaskFn = Callable[[Dict[str, object]], object]


class DAGValidationError(ValueError):
    """Raised when the task graph is malformed (duplicate, missing dep, cycle)."""


@dataclass
class Task:
    name: str
    fn: TaskFn
    deps: Tuple[str, ...] = ()


@dataclass
class TaskState:
    status: Status = Status.PENDING
    error: Optional[BaseException] = None
    attempts: int = 0
    skip_reasons: Tuple[Tuple[str, str], ...] = ()


@dataclass
class FailFastReport:
    """Result of the "before isolation" baseline.

    ``aborted`` counts tasks that were never started because one earlier task
    failed and the whole pipeline was torn down (naive orchestration).
    """

    statuses: Dict[str, Status]
    order: List[str]
    failed_at: Optional[str]
    succeeded: int
    failed: int
    aborted: int
    completion_rate: float


@dataclass
class Report:
    statuses: Dict[str, Status]
    order: List[str]
    succeeded: int
    failed: int
    skipped: int
    pending: int
    completion_rate: float
    skip_reasons: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    failures: Dict[str, BaseException] = field(default_factory=dict)


class FailureIsolationEngine:
    def __init__(self, tasks: Iterable[Task]):
        self.tasks: Dict[str, Task] = {}
        self.children: Dict[str, List[str]] = defaultdict(list)
        for task in tasks:
            if task.name in self.tasks:
                raise DAGValidationError(f"duplicate task name: {task.name}")
            self.tasks[task.name] = task
        for name, task in self.tasks.items():
            for dep in task.deps:
                if dep not in self.tasks:
                    raise DAGValidationError(
                        f"task {name!r} depends on unknown task {dep!r}"
                    )
                self.children[dep].append(name)
        self._order = self._topo_sort()
        self.state: Dict[str, TaskState] = {
            name: TaskState() for name in self.tasks
        }
        self.results: Dict[str, object] = {}
        self.run_count: Dict[str, int] = defaultdict(int)
        self.execution_order: List[str] = []

    @property
    def order(self) -> List[str]:
        return list(self._order)

    def _topo_sort(self) -> List[str]:
        indegree = {name: len(task.deps) for name, task in self.tasks.items()}
        ready = deque(name for name in self.tasks if indegree[name] == 0)
        order: List[str] = []
        while ready:
            name = ready.popleft()
            order.append(name)
            for child in self.children[name]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if len(order) != len(self.tasks):
            cyclic = [n for n, d in indegree.items() if d > 0]
            raise DAGValidationError(f"cycle detected involving: {cyclic}")
        return order

    def impact_scope(self, roots: Iterable[str]) -> Set[str]:
        """Return all tasks that transitively depend on any task in ``roots``."""
        affected: Set[str] = set()
        stack = list(roots)
        while stack:
            name = stack.pop()
            for child in self.children.get(name, ()):
                if child not in affected:
                    affected.add(child)
                    stack.append(child)
        return affected

    def _failure_roots(self, name: str) -> List[str]:
        """Root upstream failed tasks reachable from ``name`` (sorted)."""
        roots: Set[str] = set()
        seen: Set[str] = set()
        stack = list(self.tasks[name].deps)
        while stack:
            dep = stack.pop()
            if dep in seen:
                continue
            seen.add(dep)
            if self.state[dep].status is Status.FAILED:
                roots.add(dep)
            stack.extend(self.tasks[dep].deps)
        return sorted(roots)

    def _execute(self, task: Task) -> None:
        state = self.state[task.name]
        state.status = Status.RUNNING
        state.attempts += 1
        state.skip_reasons = ()
        self.run_count[task.name] += 1
        self.execution_order.append(task.name)
        try:
            result = task.fn(self.results)
        except BaseException as exc:  # noqa: BLE001 - failures must be isolated
            state.status = Status.FAILED
            state.error = exc
            return
        state.status = Status.SUCCESS
        state.error = None
        self.results[task.name] = result

    def _skip(self, name: str) -> None:
        roots = self._failure_roots(name)
        state = self.state[name]
        state.status = Status.SKIPPED
        state.skip_reasons = tuple(
            (root, str(self.state[root].error)) for root in roots
        )

    def _schedule(self) -> None:
        for name in self._order:
            state = self.state[name]
            if state.status is not Status.PENDING:
                continue
            if any(
                self.state[dep].status is not Status.SUCCESS
                for dep in self.tasks[name].deps
            ):
                self._skip(name)
            else:
                self._execute(self.tasks[name])

    def run(self) -> Report:
        if any(s.status is not Status.PENDING for s in self.state.values()):
            raise RuntimeError("engine already ran; use retry() after a fix")
        self._schedule()
        return self.report()

    def retry(
        self,
        fixes: Dict[str, TaskFn],
        only: Optional[Iterable[str]] = None,
    ) -> Report:
        """Re-run only the subgraph affected by fixed failed tasks.

        ``fixes`` maps failed task names to repaired callables. When ``only``
        is given, just those failed tasks are repaired; descendants are still
        included automatically. SUCCESS tasks are never touched.
        """
        targets = list(only) if only is not None else list(fixes)
        if not targets:
            raise ValueError("retry requires at least one failed task to fix")
        for name in targets:
            if name not in self.tasks:
                raise DAGValidationError(f"unknown task: {name}")
            if self.state[name].status is not Status.FAILED:
                raise ValueError(
                    f"task {name!r} is {self.state[name].status.value}, "
                    "only FAILED tasks can be retried"
                )
        for name, fn in fixes.items():
            if name not in self.tasks:
                raise DAGValidationError(f"unknown task: {name}")
            self.tasks[name] = Task(name, fn, self.tasks[name].deps)

        affected = self.impact_scope(targets)
        for name in affected | set(targets):
            state = self.state[name]
            if state.status is Status.SUCCESS:
                continue
            state.status = Status.PENDING
            state.error = None
            state.attempts = 0
            state.skip_reasons = ()
        self._schedule()
        return self.report()

    def explain_skip(self, name: str) -> List[Tuple[str, str]]:
        """Return [(failed_upstream, error_message), ...] for a skipped task."""
        state = self.state[name]
        if state.status is not Status.SKIPPED:
            raise KeyError(f"task {name!r} is not skipped")
        return list(state.skip_reasons)

    def report(self) -> Report:
        counter: Dict[Status, int] = defaultdict(int)
        for state in self.state.values():
            counter[state.status] += 1
        total = len(self.tasks)
        skip_reasons = {
            name: list(state.skip_reasons)
            for name, state in self.state.items()
            if state.status is Status.SKIPPED
        }
        failures = {
            name: state.error
            for name, state in self.state.items()
            if state.status is Status.FAILED and state.error is not None
        }
        return Report(
            statuses={name: s.status for name, s in self.state.items()},
            order=list(self.execution_order),
            succeeded=counter[Status.SUCCESS],
            failed=counter[Status.FAILED],
            skipped=counter[Status.SKIPPED],
            pending=counter[Status.PENDING] + counter[Status.RUNNING],
            completion_rate=(counter[Status.SUCCESS] / total if total else 1.0),
            skip_reasons=skip_reasons,
            failures=failures,
        )


def simulate_failfast(tasks: Iterable[Task]) -> FailFastReport:
    """Naive baseline: execute topo order, abort everything on first failure."""
    engine = FailureIsolationEngine(tasks)
    statuses: Dict[str, Status] = {}
    order: List[str] = []
    failed_at: Optional[str] = None
    for name in engine.order:
        if failed_at is not None:
            statuses[name] = Status.SKIPPED
            continue
        task = engine.tasks[name]
        order.append(name)
        try:
            task.fn({})
            statuses[name] = Status.SUCCESS
        except BaseException as exc:  # noqa: BLE001 - whole pipeline tears down
            statuses[name] = Status.FAILED
            failed_at = name
            _ = exc
    succeeded = sum(1 for s in statuses.values() if s is Status.SUCCESS)
    failed = 1 if failed_at is not None else 0
    aborted = sum(1 for s in statuses.values() if s is Status.SKIPPED)
    total = len(statuses)
    return FailFastReport(
        statuses=statuses,
        order=order,
        failed_at=failed_at,
        succeeded=succeeded,
        failed=failed,
        aborted=aborted,
        completion_rate=(succeeded / total if total else 1.0),
    )
