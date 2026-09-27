"""统一时钟抽象。

两种时间语义：

- wall（墙上时间 / 真实世界时间）：人类可读的绝对时间，对应 ``time.time()``。
  可能被 NTP 或人工校时调整，会回拨或跳变。只用于"在某个真实时间点过期"
  这类语义，例如 token 的 exp、缓存项在墙上时间 X 失效、生成时间戳日志。
  严禁用它测量经过时长、做超时/重试等待。

- mono（单调时间）：对应 ``time.monotonic_ns()``，保证在单台机器的同一
  进程内单调不减（墙上回拨对它无影响）。用于超时、deadline、重试退避等待、
  速率限制等所有"经过多久"的判断。

所有需要读时间的代码都必须通过注入的 :class:`Clock` 读取，
不得直接调用 ``time.time()`` / ``time.monotonic()``。
"""

from __future__ import annotations

import threading
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """时钟接口：区分墙上时间与单调时间。"""

    def wall_now(self) -> float:
        """当前墙上时间，epoch 秒，可能回拨。"""

    def mono_now(self) -> float:
        """当前单调时间，秒；对同一读取者保证不回退。"""

    def sleep(self, seconds: float) -> None:
        """等待 ``seconds`` 秒；假时钟下为立即推进，绝无真实等待。"""


class SystemClock:
    """真实系统时钟。生产环境单例。

    单调读数基于 ``monotonic_ns()``。系统级单调时钟只保证"自启动后单调"，
    本类通过每读取者 max() 钳制，额外保证多线程中每个调用点读到的值不会
    比同一线程上一次读到的小，也不会因内部取整出现倒退。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_mono_ns = time.monotonic_ns()

    def wall_now(self) -> float:
        return time.time()

    def mono_now(self) -> float:
        now_ns = time.monotonic_ns()
        with self._lock:
            if now_ns < self._last_mono_ns:
                now_ns = self._last_mono_ns
            else:
                self._last_mono_ns = now_ns
            return now_ns / 1_000_000_000.0

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sleep length must be non-negative")
        time.sleep(seconds)


SYSTEM_CLOCK = SystemClock()


class FakeClock:
    """可注入的假时钟，时间只在被显式推进时才流动。

    - ``mono`` 只增不减（任何会使其倒退的操作都会被拒绝），因此超时、
      过期（基于 mono 的）与重试退避的判定完全确定。
    - ``wall`` 允许被任意设置（含回拨），用于验证墙上时间回拨时
      绝对过期点的行为。
    - :meth:`sleep` 立即把单调时间推进 ``seconds`` 并唤醒其他在
      :meth:`advance` / :meth:`sleep` 上等待的线程；测试不发生任何真实等待。
    """

    def __init__(self, start_wall: float = 1_000_000.0, start_mono: float = 0.0) -> None:
        if start_mono < 0:
            raise ValueError("start_mono must be non-negative")
        self._cond = threading.Condition(lock=threading.RLock())
        self._wall = float(start_wall)
        self._mono = float(start_mono)

    def wall_now(self) -> float:
        with self._cond:
            return self._wall

    def mono_now(self) -> float:
        with self._cond:
            return self._mono

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sleep length must be non-negative")
        if seconds > 0:
            self.advance(seconds)

    def advance(self, seconds: float) -> float:
        """单调时间前进 ``seconds``（必须为非负），唤醒等待者，返回新值。"""
        if seconds < 0:
            raise ValueError("cannot advance clock backwards")
        with self._cond:
            self._mono += float(seconds)
            self._wall += float(seconds)
            self._cond.notify_all()
            return self._mono

    def wait_until(self, target_mono: float, real_timeout: float = 5.0) -> float:
        """阻塞当前线程，直到假单调时间 >= ``target_mono``。

        供多线程确定性测试使用：本线程不消耗任何真实时长（仅等待条件
        变量），由其他线程调用 :meth:`advance` 唤醒。``real_timeout``
        只是测试安全网，防止驱动逻辑写错后永久挂死。
        """
        with self._cond:
            reached = self._cond.wait_for(
                lambda: self._mono >= target_mono, timeout=real_timeout)
            if not reached:
                raise TimeoutError(
                    f"fake clock never reached {target_mono}; test driver "
                    f"did not advance it (now {self._mono})")
            return self._mono

    def set_wall(self, wall: float) -> float:
        """直接设置墙上时间（允许回拨）；单调时间不受影响。"""
        with self._cond:
            self._wall = float(wall)
            self._cond.notify_all()
            return self._wall

    def set_mono(self, mono: float) -> float:
        """直接设置单调时间；只允许设为不小于当前值。"""
        with self._cond:
            if mono < self._mono:
                raise ValueError("monotonic time can never move backwards")
            self._mono = float(mono)
            self._cond.notify_all()
            return self._mono
