"""固定周期任务调度器（修复版）。

时间完全由 Clock 注入，测试可确定性驱动。

补偿策略（CatchUpPolicy）：
    ALL     停机期间错过的触发点按时间顺序全部补跑；
    LATEST  只补跑最近一个错过点，更早的点记 suppressed（留痕但不告警）；
    SKIP    完全不补跑，错过点记 missed_skip，计数并告警。

幂等保证：
    每个触发点 = (job, seq)，seq 从启动锚点起按 interval 单调编号。
    FIRE 事件在任务函数执行前先写入事件流（write-ahead），游标同时前移；
    因此同一触发点最多执行一次，即使任务抛异常或再次 pump 也不会重放。

重叠保证：
    pump 用同一把锁串行化状态变更；任务函数在锁外执行，执行期间该任务
    再次到期一律记 overlap（计数 + 告警），同一时刻绝不会并发执行同一任务。
"""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

_EPS = 1e-9


class CatchUpPolicy(str, enum.Enum):
    ALL = "ALL"
    LATEST = "LATEST"
    SKIP = "SKIP"


class Disposition(str, enum.Enum):
    FIRE = "fire"                # 实际执行（正常或补跑）
    MISSED_SKIP = "missed_skip"  # SKIP 策略：停机错过，跳过并告警
    SUPPRESSED = "suppressed"    # LATEST 策略：被折叠的较早错过点（不告警）
    OVERLAP = "overlap"          # 上一次执行未结束，该点被跳过（计数并告警）


@dataclass(frozen=True)
class JobContext:
    job: str
    seq: int
    scheduled_at: float
    now: float
    is_catch_up: bool


@dataclass
class TriggerEvent:
    job: str
    seq: int
    scheduled_at: float
    observed_at: float
    disposition: Disposition
    is_catch_up: bool = False
    alert: Optional[str] = None
    error: Optional[str] = None
    result: Any = None


class VirtualClock:
    """测试用时钟：只有显式 set/advance 才会走时。"""

    def __init__(self, t: float = 0.0) -> None:
        self._t = float(t)
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._t

    def set(self, t: float) -> float:
        with self._lock:
            self._t = float(t)
            return self._t

    def advance(self, dt: float) -> float:
        with self._lock:
            self._t += float(dt)
            return self._t


class MonotonicClock:
    """生产用时钟：单调时钟，不受系统墙上时间回拨影响。"""

    def now(self) -> float:
        return time.monotonic()


@dataclass
class _Job:
    name: str
    interval: float
    func: Callable[[JobContext], Any]
    start: float
    policy: CatchUpPolicy = CatchUpPolicy.ALL
    next_seq: int = 0
    running: bool = False

    def fire_time(self, seq: int) -> float:
        return self.start + seq * self.interval


class Scheduler:
    def __init__(
        self,
        clock: Any,
        on_alert: Optional[Callable[[str, List[TriggerEvent]], None]] = None,
    ) -> None:
        self.clock = clock
        self.events: List[TriggerEvent] = []
        self.alerts: List[str] = []
        self._jobs: Dict[str, _Job] = {}
        self._order: List[str] = []
        self._lock = threading.RLock()
        self._last_now: Optional[float] = None
        self._on_alert = on_alert

    def add_job(
        self,
        name: str,
        interval: float,
        func: Callable[[JobContext], Any],
        start: Optional[float] = None,
        policy: CatchUpPolicy = CatchUpPolicy.ALL,
    ) -> None:
        with self._lock:
            if name in self._jobs:
                raise ValueError(f"job already exists: {name}")
            first = float(start) if start is not None else float(self.clock.now())
            job = _Job(
                name=name,
                interval=float(interval),
                func=func,
                start=first,
                policy=CatchUpPolicy(policy),
            )
            self._jobs[name] = job
            self._order.append(name)

    def pump(self, now: Optional[float] = None) -> List[TriggerEvent]:
        """驱动一次调度：处理所有在 now 时刻（含）之前到期的触发点。

        停机恢复后重复/补调 pump 即可，策略决定错过点如何处置。
        返回本次新产生的事件（明细）。
        """
        with self._lock:
            current = float(self.clock.now()) if now is None else float(now)
            # 单调时钟保护：回拨直接忽略，避免同一批点被二次处理。
            if self._last_now is not None and current < self._last_now - _EPS:
                return []
            self._last_now = current

            prepared: List[Tuple[_Job, List[Tuple[int, float, bool, TriggerEvent]]]] = []
            batch: List[TriggerEvent] = []
            for name in self._order:
                job = self._jobs[name]
                due: List[Tuple[int, float]] = []
                while True:
                    t = job.fire_time(job.next_seq)
                    if t > current + _EPS:
                        break
                    due.append((job.next_seq, t))
                    job.next_seq += 1
                if not due:
                    continue

                if job.running:
                    # 上一次执行尚未结束：任何到期点（无论是否错过）一律
                    # overlap，跳过 + 计数 + 告警，游标照常前移。
                    evs = [
                        TriggerEvent(job.name, seq, t, current, Disposition.OVERLAP)
                        for seq, t in due
                    ]
                    self._raise_alert(
                        job,
                        f"job={job.name} previous run still in progress; "
                        f"skipped {len(evs)} trigger(s) seq={evs[0].seq}..{evs[-1].seq}",
                        evs,
                    )
                    self.events.extend(evs)
                    batch.extend(evs)
                    prepared.append((job, []))
                    continue

                missed = [(s, t) for s, t in due if t < current - _EPS]
                on_time = [(s, t) for s, t in due if abs(t - current) <= _EPS]

                note_events: List[TriggerEvent] = []
                fires: List[Tuple[int, float, bool]] = []

                if missed:
                    if job.policy is CatchUpPolicy.SKIP:
                        note_events = [
                            TriggerEvent(job.name, s, t, current,
                                         Disposition.MISSED_SKIP)
                            for s, t in missed
                        ]
                        self._raise_alert(
                            job,
                            f"job={job.name} downtime skipped {len(missed)} "
                            f"trigger(s) seq={missed[0][0]}..{missed[-1][0]} "
                            f"scheduled={missed[0][1]}..{missed[-1][1]}",
                            note_events,
                        )
                    elif job.policy is CatchUpPolicy.LATEST:
                        for s, t in missed[:-1]:
                            note_events.append(
                                TriggerEvent(job.name, s, t, current,
                                             Disposition.SUPPRESSED)
                            )
                        s, t = missed[-1]
                        fires.append((s, t, True))
                    else:  # CatchUpPolicy.ALL
                        for s, t in missed:
                            fires.append((s, t, True))

                for s, t in on_time:
                    fires.append((s, t, False))

                # write-ahead：先落 FIRE 事件并前移游标，再执行副作用，
                # 保证 (job, seq) 至多一次副作用。
                fire_records: List[Tuple[int, float, bool, TriggerEvent]] = []
                for s, t, is_catch_up in fires:
                    ev = TriggerEvent(
                        job.name, s, t, current, Disposition.FIRE,
                        is_catch_up=is_catch_up,
                    )
                    note_events.append(ev)
                    fire_records.append((s, t, is_catch_up, ev))
                self.events.extend(note_events)
                batch.extend(note_events)
                job.running = bool(fire_records)
                prepared.append((job, fire_records))

        # 副作用在锁外执行，避免长时间任务阻塞其它任务的状态结算。
        for job, fire_records in prepared:
            try:
                for seq, scheduled_at, is_catch_up, ev in fire_records:
                    ctx = JobContext(
                        job=job.name,
                        seq=seq,
                        scheduled_at=scheduled_at,
                        now=ev.observed_at,
                        is_catch_up=is_catch_up,
                    )
                    try:
                        ev.result = job.func(ctx)
                    except Exception as exc:  # 失败留痕，不重放，不影响后续点
                        ev.error = f"{type(exc).__name__}: {exc}"
            finally:
                with self._lock:
                    job.running = False

        return batch

    def _raise_alert(self, job: _Job, message: str,
                     evs: List[TriggerEvent]) -> None:
        self.alerts.append(message)
        for ev in evs:
            ev.alert = message
        if self._on_alert is not None:
            self._on_alert(message, evs)

    def counts(self) -> Dict[str, int]:
        with self._lock:
            result: Dict[str, int] = {d.value: 0 for d in Disposition}
            for ev in self.events:
                result[ev.disposition.value] += 1
            return result

    def fired_seqs(self, job: str) -> List[int]:
        with self._lock:
            return [ev.seq for ev in self.events
                    if ev.job == job and ev.disposition is Disposition.FIRE]


class SchedulerRunner:
    """轻量常驻线程：按注入时钟周期性 pump。

    仅适合 MonotonicClock（真实睡眠）。线程 stop 可即时唤醒。
    """

    def __init__(self, scheduler: Scheduler, max_sleep: float = 0.05) -> None:
        self.scheduler = scheduler
        self.max_sleep = max_sleep
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="scheduler-runner",
                                        daemon=True)
        self._thread.start()

    def run(self) -> None:
        while not self._stop.is_set():
            self.scheduler.pump()
            timeout = self.max_sleep
            with self.scheduler._lock:
                pending = [
                    self.scheduler._jobs[n].fire_time(
                        self.scheduler._jobs[n].next_seq
                    )
                    - self.scheduler.clock.now()
                    for n in self.scheduler._order
                ]
            if pending:
                timeout = min(timeout, max(0.0, min(pending)))
            self._stop.wait(timeout)

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
