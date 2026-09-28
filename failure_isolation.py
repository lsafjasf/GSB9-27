"""失败隔离的 DAG 任务编排库（仅标准库）。

核心语义：
- 任务按依赖关系（DAG）调度执行。
- 某任务失败时，仅其"下游传递闭包"被跳过（skip），无关分支继续执行。
- 每个被跳过的任务记录原因：是哪个（些）上游失败导致的。
- 支持局部重试：修复后调用 resume()，只重跑 failed/skipped 任务，
  已 completed 的任务绝不重复执行。
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Set


class Status(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskResult:
    name: str
    status: Status
    value: object = None
    error: Optional[str] = None
    # 若被跳过：导致跳过的"根失败"上游任务名集合
    skip_reasons: Set[str] = field(default_factory=set)


class DAGError(ValueError):
    pass


class Orchestrator:
    """按依赖图执行任务，失败只隔离受影响子图。"""

    def __init__(self) -> None:
        self._funcs: Dict[str, Callable[[], object]] = {}
        self._deps: Dict[str, Set[str]] = {}          # task -> 直接依赖
        self._dependents: Dict[str, Set[str]] = defaultdict(set)
        self.results: Dict[str, TaskResult] = {}
        self.run_counts: Dict[str, int] = defaultdict(int)  # 实际执行次数（重试检测用）

    # ------------------------------------------------------------------ 定义
    def add_task(self, name: str, fn: Callable[[], object],
                 deps: Optional[List[str]] = None) -> None:
        if name in self._funcs:
            raise DAGError(f"duplicate task: {name}")
        self._funcs[name] = fn
        self._deps[name] = set(deps or [])
        for d in self._deps[name]:
            self._dependents[d].add(name)
        self.results[name] = TaskResult(name=name, status=Status.PENDING)

    def _validate(self) -> None:
        for name, deps in self._deps.items():
            for d in deps:
                if d not in self._funcs:
                    raise DAGError(f"task {name!r} depends on unknown task {d!r}")
        self._topo_order()  # 顺带做环检测

    def _topo_order(self) -> List[str]:
        indeg = {n: len(self._deps[n]) for n in self._funcs}
        ready = deque(sorted(n for n, dg in indeg.items() if dg == 0))
        order: List[str] = []
        while ready:
            n = ready.popleft()
            order.append(n)
            for m in sorted(self._dependents[n]):
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
        if len(order) != len(self._funcs):
            raise DAGError("dependency cycle detected")
        return order

    # ------------------------------------------------------- 影响传播计算
    def affected_by(self, failed: Set[str]) -> Set[str]:
        """失败集合的下游传递闭包（不含失败任务本身）。"""
        affected: Set[str] = set()
        stack = list(failed)
        while stack:
            cur = stack.pop()
            for nxt in self._dependents[cur]:
                if nxt not in affected:
                    affected.add(nxt)
                    stack.append(nxt)
        return affected

    def _root_failed_ancestors(self, name: str) -> Set[str]:
        """向上回溯，找出导致 name 被跳过的所有 FAILED 祖先。"""
        roots: Set[str] = set()
        stack = [name]
        seen: Set[str] = set()
        while stack:
            cur = stack.pop()
            for d in self._deps[cur]:
                if d in seen:
                    continue
                seen.add(d)
                st = self.results[d].status
                if st is Status.FAILED:
                    roots.add(d)
                elif st is Status.SKIPPED:
                    stack.append(d)  # 继续向上找根失败
        return roots

    # ------------------------------------------------------------------ 执行
    def run(self) -> "Orchestrator":
        """全量执行（首次运行）。失败任务抛出异常即视为失败。"""
        self._validate()
        for name in self._topo_order():
            self._run_one(name)
        return self

    def resume(self, fixes: Optional[Dict[str, Callable[[], object]]] = None
               ) -> "Orchestrator":
        """局部重试：只重跑 FAILED / SKIPPED 任务，已完成任务绝不重跑。

        fixes: {任务名: 修复后的可调用对象}，用于替换原先失败的实现。
        """
        for name, fn in (fixes or {}).items():
            if name not in self._funcs:
                raise DAGError(f"cannot fix unknown task: {name!r}")
            self._funcs[name] = fn

        # 先把可重跑的任务重置为 PENDING
        for res in self.results.values():
            if res.status in (Status.FAILED, Status.SKIPPED):
                res.status = Status.PENDING
                res.error = None
                res.skip_reasons = set()

        for name in self._topo_order():
            if self.results[name].status is Status.PENDING:
                self._run_one(name)
        return self

    def _run_one(self, name: str) -> None:
        res = self.results[name]
        bad_deps = [d for d in self._deps[name]
                    if self.results[d].status in (Status.FAILED, Status.SKIPPED)]
        if bad_deps:
            res.status = Status.SKIPPED
            res.skip_reasons = self._root_failed_ancestors(name)
            return
        try:
            self.run_counts[name] += 1
            res.value = self._funcs[name]()
            res.status = Status.COMPLETED
        except Exception as exc:  # noqa: BLE001 - 任务异常即失败
            res.status = Status.FAILED
            res.error = f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------ 统计
    def stats(self) -> Dict[str, object]:
        by = {s: [] for s in Status}
        for res in self.results.values():
            by[res.status].append(res.name)
        total = len(self.results)
        return {
            "total": total,
            "completed": sorted(by[Status.COMPLETED]),
            "failed": sorted(by[Status.FAILED]),
            "skipped": sorted(by[Status.SKIPPED]),
            "pending": sorted(by[Status.PENDING]),
            "completion_rate": len(by[Status.COMPLETED]) / total if total else 1.0,
            "skip_reasons": {
                n: sorted(self.results[n].skip_reasons)
                for n in by[Status.SKIPPED]
            },
        }

    def report(self, title: str = "") -> str:
        s = self.stats()
        lines = [f"== {title} ==" if title else "== report =="]
        lines.append(f"completed: {len(s['completed'])}/{s['total']} "
                     f"({s['completion_rate']:.0%})  {s['completed']}")
        lines.append(f"failed:    {len(s['failed'])}  {s['failed']}")
        lines.append(f"skipped:   {len(s['skipped'])}  {s['skipped']}")
        for name, reasons in s["skip_reasons"].items():
            lines.append(f"  skip {name}: caused by failed upstream {reasons}")
        return "\n".join(lines)
