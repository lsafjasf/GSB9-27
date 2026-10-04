"""会话生命周期核心实现。

设计要点：
- 空闲超时：以最后一次活动时间 last_activity_at 为基准，
  idle_expires_at = last_activity_at + idle_timeout。
- 绝对超时：以建立时刻 created_at 为基准，
  absolute_expires_at = created_at + absolute_timeout，续期永远不改变它。
- 实际到期时刻 effective_expires_at = min(idle_expires_at, absolute_expires_at)。
- 判定规则：now >= effective_expires_at 视为已过期（边界即过期）。
- 时间可注入：构造时传入 clock，或每次调用显式传 now。
- 并发续期：内部用锁串行化，last_activity_at 只前进不后退（取 max），
  因此并发续期的最终到期时刻是确定性的，不依赖线程调度顺序。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class SessionState(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"


class ExpireReason(str, Enum):
    NONE = "none"
    IDLE_TIMEOUT = "idle_timeout"          # 空闲超时：now >= idle_expires_at
    ABSOLUTE_TIMEOUT = "absolute_timeout"  # 绝对超时：now >= absolute_expires_at


class SessionExpiredError(Exception):
    """对已过期会话执行续期时抛出。"""

    def __init__(self, reason: ExpireReason, snapshot: "LifecycleSnapshot"):
        self.reason = reason
        self.snapshot = snapshot
        super().__init__(f"session already expired: {reason.value}")


@dataclass(frozen=True)
class SessionConfig:
    idle_timeout: float      # 空闲超时秒数，> 0
    absolute_timeout: float  # 绝对超时秒数，> 0

    def __post_init__(self) -> None:
        if self.idle_timeout <= 0:
            raise ValueError("idle_timeout must be > 0")
        if self.absolute_timeout <= 0:
            raise ValueError("absolute_timeout must be > 0")


@dataclass(frozen=True)
class LifecycleSnapshot:
    """某一时刻的完整生命周期判定数据。"""

    now: float
    created_at: float
    last_activity_at: float
    idle_timeout: float
    absolute_timeout: float
    idle_expires_at: float       # 空闲到期时刻
    absolute_expires_at: float   # 绝对到期时刻
    effective_expires_at: float  # 实际到期时刻 = min(两者)
    idle_remaining: float        # 距空闲到期的剩余秒数（可为负）
    absolute_remaining: float    # 距绝对到期的剩余秒数（可为负）
    state: SessionState
    reason: ExpireReason


@dataclass(frozen=True)
class RenewalResult:
    """一次续期的前后对照数据。"""

    renewed_at: float
    before: LifecycleSnapshot    # 续期前判定数据
    after: LifecycleSnapshot     # 续期后判定数据

    @property
    def idle_expiry_extended_by(self) -> float:
        return self.after.idle_expires_at - self.before.idle_expires_at

    @property
    def absolute_expiry_unchanged(self) -> bool:
        return self.after.absolute_expires_at == self.before.absolute_expires_at


class Session:
    """单个会话。线程安全。"""

    def __init__(
        self,
        config: SessionConfig,
        clock: Callable[[], float] = time.monotonic,
        now: Optional[float] = None,
    ) -> None:
        self._config = config
        self._clock = clock
        created = clock() if now is None else now
        self._created_at = created
        self._last_activity_at = created
        self._lock = threading.Lock()

    # ---- 内部 ----

    def _resolve_now(self, now: Optional[float]) -> float:
        return self._clock() if now is None else now

    def _judge(self, now: float, last_activity_at: float) -> LifecycleSnapshot:
        idle_expires_at = last_activity_at + self._config.idle_timeout
        absolute_expires_at = self._created_at + self._config.absolute_timeout
        effective = min(idle_expires_at, absolute_expires_at)
        if now >= absolute_expires_at:
            state, reason = SessionState.EXPIRED, ExpireReason.ABSOLUTE_TIMEOUT
        elif now >= idle_expires_at:
            state, reason = SessionState.EXPIRED, ExpireReason.IDLE_TIMEOUT
        else:
            state, reason = SessionState.ACTIVE, ExpireReason.NONE
        return LifecycleSnapshot(
            now=now,
            created_at=self._created_at,
            last_activity_at=last_activity_at,
            idle_timeout=self._config.idle_timeout,
            absolute_timeout=self._config.absolute_timeout,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
            effective_expires_at=effective,
            idle_remaining=idle_expires_at - now,
            absolute_remaining=absolute_expires_at - now,
            state=state,
            reason=reason,
        )

    # ---- 公开 API ----

    def inspect(self, now: Optional[float] = None) -> LifecycleSnapshot:
        """返回当前时刻的生命周期判定数据（只读）。"""
        t = self._resolve_now(now)
        with self._lock:
            return self._judge(t, self._last_activity_at)

    def is_alive(self, now: Optional[float] = None) -> bool:
        return self.inspect(now).state is SessionState.ACTIVE

    def renew(self, now: Optional[float] = None) -> RenewalResult:
        """续期：只推进空闲到期时刻，绝不延长绝对期限。

        并发安全：锁内完成“判定 + 推进”，多个并发续期串行执行，
        last_activity_at 取 max（只前进不后退），最终到期时刻确定。
        已过期会话拒绝续期，抛 SessionExpiredError。
        """
        t = self._resolve_now(now)
        with self._lock:
            before = self._judge(t, self._last_activity_at)
            if before.state is SessionState.EXPIRED:
                raise SessionExpiredError(before.reason, before)
            # 只前进不后退：时钟回拨或乱序到达的续期不会覆盖更新的值
            self._last_activity_at = max(self._last_activity_at, t)
            after = self._judge(t, self._last_activity_at)
            return RenewalResult(renewed_at=t, before=before, after=after)

    @property
    def created_at(self) -> float:
        return self._created_at

    @property
    def absolute_expires_at(self) -> float:
        return self._created_at + self._config.absolute_timeout
