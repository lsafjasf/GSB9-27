"""修复后的定周期调度器：停机恢复后按策略补偿错过的触发点。

设计要点：
- 时钟由外部注入（clock 可调用对象），测试可自由推进/回拨时间。
- 触发点严格按 last + interval 递推，不锚定到 now，停机跨过的每个
  触发点都会被发现并按 MisfirePolicy 处理。
- 幂等：每个触发点以 (task, scheduled_at) 为键，已执行的触发点绝不
  重复产生副作用，重复触发只留下 duplicate_suppressed 记录。
- 同一任务的执行通过 per-task 锁串行化；任务运行期间到期的新触发点
  进入 pending 队列延后执行，绝不并发执行同一任务。
"""
from __future__ import annotations

import enum
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional


class MisfirePolicy(enum.Enum):
    """停机/卡顿期间错过的触发点的补偿策略。"""

    RUN_ALL = "run_all"        # 全部补跑：适用于幂等的统计/对账类任务
    RUN_LATEST = "run_latest"  # 只补最近一次：适用于全量刷新类任务（状态覆盖式）
    SKIP = "skip"              # 完全跳过：适用于过期即无意义的任务；跳过会计数并告警


@dataclass(frozen=True)
class TriggerRecord:
    """一次触发（或被抑制的重复触发）的可断言记录。"""

    task: str
    scheduled_at: int   # 触发点（幂等键的一部分）
    started_at: int
    finished_at: int
    status: str         # "executed" | "duplicate_suppressed"


@dataclass(frozen=True)
class MissedEvent:
    """被策略跳过/合并/延迟的触发点记录。"""

    task: str
    scheduled_at: int
    reason: str  # "skipped_by_policy" | "superseded_by_latest" | "deferred_overlap"


class Task:
    def __init__(
        self,
        name: str,
        interval: int,
        func: Callable[[int], None],
        policy: MisfirePolicy = MisfirePolicy.RUN_ALL,
    ):
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.name = name
        self.interval = interval
        self.func = func
        self.policy = policy
        self.next_trigger: Optional[int] = None
        self.executed: set[int] = set()      # 已执行的触发点（幂等键）
        self.pending: Deque[int] = deque()   # 因重叠被延迟的触发点
        self.lock = threading.Lock()         # 保证同一任务不并发执行


class Scheduler:
    def __init__(
        self,
        clock: Callable[[], int],
        alert_handler: Optional[Callable[[str], None]] = None,
    ):
        self.clock = clock
        self.tasks: Dict[str, Task] = {}
        self.records: List[TriggerRecord] = []
        self.missed: List[MissedEvent] = []
        self.alerts: List[str] = []
        self._alert_handler = alert_handler

    # ------------------------------------------------------------------ API

    def register(self, task: Task, start_at: Optional[int] = None) -> None:
        task.next_trigger = start_at if start_at is not None else self.clock() + task.interval
        self.tasks[task.name] = task

    def tick(self) -> None:
        """推进一次调度循环。由外部按任意频率调用。"""
        now = self.clock()
        for task in self.tasks.values():
            self._process(task, now)

    def trigger_detail(self) -> List[dict]:
        """触发明细：每次执行/抑制/跳过的完整记录，供审计与断言。"""
        detail = [
            {
                "task": r.task,
                "scheduled_at": r.scheduled_at,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "status": r.status,
            }
            for r in self.records
        ]
        detail += [
            {
                "task": m.task,
                "scheduled_at": m.scheduled_at,
                "started_at": None,
                "finished_at": None,
                "status": m.reason,
            }
            for m in self.missed
        ]
        return sorted(detail, key=lambda d: (d["scheduled_at"], d["task"]))

    # ------------------------------------------------------------- internal

    def _alert(self, message: str) -> None:
        self.alerts.append(message)
        if self._alert_handler is not None:
            self._alert_handler(message)

    def _process(self, task: Task, now: int) -> None:
        due: List[int] = []
        while task.next_trigger is not None and task.next_trigger <= now:
            due.append(task.next_trigger)
            task.next_trigger += task.interval

        if task.policy is MisfirePolicy.RUN_ALL:
            to_run, dropped, reason = due, [], ""
        elif task.policy is MisfirePolicy.RUN_LATEST:
            to_run, dropped, reason = due[-1:], due[:-1], "superseded_by_latest"
        else:  # SKIP
            to_run, dropped, reason = [], due, "skipped_by_policy"

        for ts in dropped:
            self.missed.append(MissedEvent(task.name, ts, reason))
        if dropped and task.policy is MisfirePolicy.SKIP:
            self._alert(
                f"[SKIP] task={task.name} dropped {len(dropped)} trigger(s): "
                f"{dropped[0]}..{dropped[-1]}"
            )

        # 上一轮因重叠延迟的触发点优先补跑；任务仍占用锁时继续留在队列中
        while task.pending and not task.lock.locked():
            self._execute(task, task.pending.popleft())

        for ts in to_run:
            self._execute(task, ts)

    def _execute(self, task: Task, scheduled_at: int) -> None:
        # 幂等：同一触发点绝不执行第二次
        if scheduled_at in task.executed:
            self.records.append(
                TriggerRecord(task.name, scheduled_at, self.clock(), self.clock(),
                              "duplicate_suppressed")
            )
            return

        # 重叠保护：任务仍在运行时，新触发点延后而非并发执行
        if not task.lock.acquire(blocking=False):
            task.pending.append(scheduled_at)
            self.missed.append(MissedEvent(task.name, scheduled_at, "deferred_overlap"))
            return
        try:
            task.executed.add(scheduled_at)
            started = self.clock()
            task.func(scheduled_at)
            finished = self.clock()
            self.records.append(
                TriggerRecord(task.name, scheduled_at, started, finished, "executed")
            )
        finally:
            task.lock.release()
