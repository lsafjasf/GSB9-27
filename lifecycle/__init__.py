"""临时对象生命周期管理（惰性过期 + 分批物理清理 + 并发安全）。

只依赖 Python 3 标准库。时间通过 clock 注入：任何无参、返回单调整数/浮点
时间戳的可调用对象都可作为时钟（时间单位由调用方自行约定，TTL 使用同一
单位）。
"""

from .store import (
    LifecycleStore,
    SweepResult,
    Stats,
    MonotonicClock,
    FakeClock,
    IntervalSweeper,
    KeyExpiredError,
)

__all__ = [
    "LifecycleStore",
    "SweepResult",
    "Stats",
    "MonotonicClock",
    "FakeClock",
    "IntervalSweeper",
    "KeyExpiredError",
]
