"""修复前的朴素调度器（仅用于复现缺口，勿用于生产）。

缺陷：
1. 恢复后只按“当前时刻”触发一次，停机期间跨过的触发点全部丢失，
   且没有任何计数/告警，业务侧只能从统计缺口倒推；
2. 每次触发立即执行，长任务期间再次到期会并发重入同一任务；
3. 触发点没有稳定标识，重复驱动 pump 可能重复产生副作用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

_EPS = 1e-9


@dataclass
class LegacyJob:
    name: str
    interval: float
    func: Callable[[float], Any]
    next_fire: float


class LegacyScheduler:
    def __init__(self, clock: Any) -> None:
        self.clock = clock
        self.fired: List[tuple] = []
        self._jobs: Dict[str, LegacyJob] = {}

    def add_job(self, name, interval, func, start=None):
        first = float(start) if start is not None else float(self.clock.now())
        self._jobs[name] = LegacyJob(name, float(interval), func, first)

    def pump(self, now=None):
        current = float(self.clock.now()) if now is None else float(now)
        batch = []
        for job in self._jobs.values():
            if current >= job.next_fire - _EPS:
                # 缺陷：只补一次，且锚点直接重置为“现在 + 周期”，
                # 中间错过的 seq 永久消失；无 missed 计数，无告警。
                job.func(current)
                self.fired.append((job.name, job.next_fire, current))
                batch.append((job.name, job.next_fire, current))
                job.next_fire = current + job.interval
        return batch
