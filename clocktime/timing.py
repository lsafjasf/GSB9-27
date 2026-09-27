"""超时、过期、重试逻辑。

重构后这些逻辑不再直接读系统时间，全部通过注入的 :class:`Clock`：

- 绝对过期（token exp、缓存条目）用墙上时间，因为对端给出的是真实时间点；
- 相对超时、deadline、重试退避用单调时间，因为关心的是"经过多久"，
  墙上时间回拨/跳变不应影响判定。
"""

from __future__ import annotations

from typing import Callable, Optional, Type, TypeVar

from .clock import Clock, SYSTEM_CLOCK

T = TypeVar("T")


class AbsoluteExpiry:
    """墙上时间上的绝对过期点（秒，epoch），例如 token 的 ``exp``。"""

    __slots__ = ("expires_at",)

    def __init__(self, expires_at_wall: float) -> None:
        self.expires_at = float(expires_at_wall)

    @classmethod
    def ttl(cls, clock: Clock, ttl_seconds: float) -> "AbsoluteExpiry":
        """从"现在 + ttl"（墙上时间）构造过期点。"""
        return cls(clock.wall_now() + ttl_seconds)

    def is_expired(self, clock: Clock, skew_seconds: float = 0.0) -> bool:
        """是否已过期。

        ``skew_seconds`` 是时钟偏移容差：仅在 ``now + skew >= exp``
        时才算过期，用来容忍本机时钟相对签发方略慢。
        """
        return clock.wall_now() + skew_seconds >= self.expires_at

    def seconds_remaining(self, clock: Clock, skew_seconds: float = 0.0) -> float:
        """剩余秒数；墙上时间回拨时可能变大，跳前时可能为负。"""
        return self.expires_at - clock.wall_now() - skew_seconds


class Timeout:
    """单调时间上的相对超时。

    超时判定基于创建时刻的单调读数，墙上时间回拨不会延长或缩短它。
    """

    __slots__ = ("deadline",)

    def __init__(self, clock: Clock, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("timeout must be non-negative")
        self.deadline = clock.mono_now() + seconds

    def expired(self, clock: Clock) -> bool:
        return clock.mono_now() >= self.deadline

    def remaining(self, clock: Clock) -> float:
        return max(0.0, self.deadline - clock.mono_now())


class RetryPolicy:
    """带封顶指数退避的重试策略。

    第 n 次失败后（n 从 1 起）等待::

        min(max_delay, base_delay * multiplier ** (n - 1))

    判定（该不该重试、下一次等多久）是纯函数，与睡眠分离，
    便于差分测试逐分支对齐。
    """

    __slots__ = ("max_attempts", "base_delay", "multiplier", "max_delay",
                 "retry_on",)

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 0.1,
        multiplier: float = 2.0,
        max_delay: float = 5.0,
        retry_on: Optional[Callable[[BaseException], bool]] = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if base_delay < 0 or max_delay < 0 or multiplier < 1:
            raise ValueError("invalid delay parameters")
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.multiplier = multiplier
        self.max_delay = max_delay
        self.retry_on = retry_on or (lambda _exc: True)

    def should_retry(self, attempt: int, exc: BaseException) -> bool:
        """``attempt`` 为已经发生的尝试次数（从 1 起）。"""
        return attempt < self.max_attempts and self.retry_on(exc)

    def delay_for(self, attempt: int) -> float:
        """第 ``attempt`` 次失败后的退避秒数。"""
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        return min(
            self.max_delay,
            self.base_delay * (self.multiplier ** (attempt - 1)),
        )


class RetryError(RuntimeError):
    """重试用尽后抛出，携带尝试次数与最后一次异常。"""

    def __init__(self, attempts: int, last_error: BaseException) -> None:
        super().__init__(f"failed after {attempts} attempt(s): {last_error!r}")
        self.attempts = attempts
        self.last_error = last_error


def run_with_retry(
    func: Callable[[], T],
    policy: RetryPolicy,
    clock: Clock = SYSTEM_CLOCK,
) -> T:
    """按 ``policy`` 重试 ``func``，所有等待走 ``clock.sleep``。"""
    attempt = 0
    while True:
        attempt += 1
        try:
            return func()
        except Exception as exc:
            if not policy.should_retry(attempt, exc):
                raise RetryError(attempt, exc)
            clock.sleep(policy.delay_for(attempt))
