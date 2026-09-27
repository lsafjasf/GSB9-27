"""复现用：有缺陷的调度实现。

缺陷：tick() 只在当前时间越过触发点时执行一次，然后把下一次触发点
直接锚定到 now + interval，停机期间跨过的所有历史触发点被静默丢弃，
导致统计缺口。
"""
from typing import Callable, Dict


class BuggyScheduler:
    def __init__(self, clock: Callable[[], int]):
        self.clock = clock
        self.intervals: Dict[str, int] = {}
        self.funcs: Dict[str, Callable[[int], None]] = {}
        self.next_trigger: Dict[str, int] = {}
        self.run_count: Dict[str, int] = {}

    def register(self, name: str, interval: int, func: Callable[[int], None]) -> None:
        self.intervals[name] = interval
        self.funcs[name] = func
        self.next_trigger[name] = self.clock() + interval
        self.run_count[name] = 0

    def tick(self) -> None:
        now = self.clock()
        for name, nxt in self.next_trigger.items():
            if nxt <= now:
                # BUG: 只补一次，且把基准点挪到 now，中间跨过的触发点全部丢失
                self.funcs[name](nxt)
                self.run_count[name] += 1
                self.next_trigger[name] = now + self.intervals[name]
