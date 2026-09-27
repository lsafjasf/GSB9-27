"""全量统计（对拍基准）：手工埋点，精确记录每个函数的自身/累计耗时。

与采样器口径对应：
- self_ns  : 函数自身消耗的时间（不含子调用）
- total_ns : 函数的累计（inclusive）时间；递归时只计最外层一次，
             与采样树的递归折叠语义一致（同一函数不重复放大）。
"""

from __future__ import annotations

import functools
import time
from collections import defaultdict


class FullProfiler:
    def __init__(self):
        self.self_ns: dict[str, int] = defaultdict(int)
        self.total_ns: dict[str, int] = defaultdict(int)
        self.calls: dict[str, int] = defaultdict(int)
        # 栈元素: [name, enter_ts, child_ns]
        self._stack: list[list] = []

    def enter(self, name: str) -> None:
        self._stack.append([name, time.perf_counter_ns(), 0])

    def exit(self, name: str) -> None:
        now = time.perf_counter_ns()
        frame = self._stack.pop()
        assert frame[0] == name, f"unbalanced enter/exit: {frame[0]} != {name}"
        elapsed = now - frame[1]
        self.self_ns[name] += elapsed - frame[2]
        self.calls[name] += 1
        # 累计时间：递归时只计最外层，避免同一函数被重复计入
        if not any(f[0] == name for f in self._stack):
            self.total_ns[name] += elapsed
        if self._stack:
            self._stack[-1][2] += elapsed

    def profile(self, name: str):
        """装饰器：把函数纳入全量统计。"""
        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                self.enter(name)
                try:
                    return fn(*args, **kwargs)
                finally:
                    self.exit(name)
            return wrapper
        return deco

    def percentages(self) -> dict[str, dict]:
        grand = sum(self.self_ns.values()) or 1
        names = set(self.self_ns) | set(self.total_ns)
        return {
            n: {
                "self_pct": 100.0 * self.self_ns.get(n, 0) / grand,
                "total_pct": 100.0 * self.total_ns.get(n, 0) / grand,
                "calls": self.calls.get(n, 0),
            }
            for n in names
        }
