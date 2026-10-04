"""可注入的时钟。

业务代码只依赖 Clock 协议（now 单调秒数 + sleep），测试时替换为 FakeClock
即可在零真实等待下验证限速重发逻辑。
"""

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """单调时间戳（秒）。"""

    def sleep(self, seconds: float) -> None:
        """等待指定秒数。"""


class SystemClock:
    """生产环境时钟。"""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock:
    """测试时钟：sleep 只推进逻辑时间，不占用真实时间。"""

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += float(seconds)
