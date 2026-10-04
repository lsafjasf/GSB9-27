"""吊销状态缓存库（仅标准库）。

设计要点：
- 每条缓存项带 TTL，过期后重新向吊销源查询；
- 容量有上界，超出时按 LRU 淘汰；
- 时间通过 time_fn 注入，便于测试与复现；
- 吊销源不可用时按显式策略处理：FAIL_CLOSED（保守拒绝）或
  FAIL_OPEN（临时放行，有过期数据时优先使用过期数据并标记 stale）；
- invalidate() 提供主动失效通道：吊销事件推送方调用后，
  刚被吊销的证书不会命中旧缓存，保证缓存与实时查询一致。
"""

from __future__ import annotations

import enum
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, Optional


class Status(enum.Enum):
    GOOD = "good"
    REVOKED = "revoked"


class Decision(enum.Enum):
    ALLOW = "allow"
    DENY = "deny"


class FailPolicy(enum.Enum):
    FAIL_CLOSED = "fail_closed"  # 保守拒绝：源不可用即拒绝
    FAIL_OPEN = "fail_open"      # 临时放行：源不可用仍放行（有过期数据先用过期数据）


class RevocationSourceError(Exception):
    """吊销源不可用（超时、连接失败、响应无效等）时由 fetcher 抛出。"""


@dataclass(frozen=True)
class Result:
    serial: str
    decision: Decision
    status: Optional[Status]   # 判定依据的状态；源不可用且无缓存时为 None
    reason: str                # 判定原因，便于审计
    from_cache: bool
    stale: bool                # 是否使用了已过期的数据
    age: float                 # 数据距抓取时刻的秒数；非缓存数据为 0


@dataclass
class Stats:
    hits: int = 0            # 未过期缓存命中
    misses: int = 0          # 未命中（含过期后重查）
    expirations: int = 0     # 因 TTL 过期而触发重查的次数
    fetches: int = 0         # 实际向吊销源发起的查询次数
    fetch_errors: int = 0    # 吊销源不可用次数
    stale_serves: int = 0    # 源不可用时使用过期数据放行的次数
    evictions: int = 0       # LRU 淘汰次数
    invalidations: int = 0   # 主动失效次数

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


@dataclass
class _Entry:
    status: Status
    fetched_at: float
    expires_at: float


class RevocationCache:
    """带 TTL、容量上界与显式失败策略的吊销状态缓存。"""

    def __init__(
        self,
        fetcher: Callable[[str], Status],
        ttl: float = 300.0,
        max_entries: int = 1024,
        policy: FailPolicy = FailPolicy.FAIL_CLOSED,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._fetcher = fetcher
        self._ttl = float(ttl)
        self._max_entries = int(max_entries)
        self._policy = policy
        self._time_fn = time_fn
        self._entries: "OrderedDict[str, _Entry]" = OrderedDict()
        self._lock = threading.Lock()
        self.stats = Stats()

    @property
    def ttl(self) -> float:
        return self._ttl

    @property
    def max_staleness(self) -> float:
        """无主动失效时，已吊销证书仍可能被放行的最坏时间窗（秒）。"""
        return self._ttl

    def __len__(self) -> int:
        return len(self._entries)

    def check(self, serial: str) -> Result:
        """查询证书是否放行。先查缓存，过期或未命中则回源。"""
        now = self._time_fn()
        with self._lock:
            entry = self._entries.get(serial)
            if entry is not None and now < entry.expires_at:
                self._entries.move_to_end(serial)
                self.stats.hits += 1
                return self._from_entry(serial, entry, now, stale=False,
                                        reason="cache_hit")
            if entry is not None:
                self.stats.expirations += 1
            self.stats.misses += 1
            self.stats.fetches += 1

        # 回源放在锁外，避免慢源阻塞其他查询
        try:
            status = self._fetcher(serial)
        except Exception:
            with self._lock:
                self.stats.fetch_errors += 1
                return self._on_source_error(serial, entry, now)

        with self._lock:
            self._store(serial, status, now)
        return Result(
            serial=serial,
            decision=Decision.ALLOW if status is Status.GOOD else Decision.DENY,
            status=status,
            reason="fetch_ok",
            from_cache=False,
            stale=False,
            age=0.0,
        )

    def invalidate(self, serial: str) -> bool:
        """主动失效：吊销事件发生时由推送方调用，返回是否有缓存被移除。"""
        with self._lock:
            self.stats.invalidations += 1
            return self._entries.pop(serial, None) is not None

    def cached_entry_info(self, serial: str) -> Optional[dict]:
        """观测用：返回某序列号的缓存项信息（含是否已过期），无缓存返回 None。"""
        now = self._time_fn()
        with self._lock:
            entry = self._entries.get(serial)
            if entry is None:
                return None
            return {
                "status": entry.status.value,
                "age": now - entry.fetched_at,
                "expired": now >= entry.expires_at,
                "expires_in": entry.expires_at - now,
            }

    # ---- 内部 ----

    def _from_entry(self, serial: str, entry: _Entry, now: float,
                    stale: bool, reason: str) -> Result:
        return Result(
            serial=serial,
            decision=Decision.ALLOW if entry.status is Status.GOOD else Decision.DENY,
            status=entry.status,
            reason=reason,
            from_cache=True,
            stale=stale,
            age=now - entry.fetched_at,
        )

    def _on_source_error(self, serial: str, entry: Optional[_Entry],
                         now: float) -> Result:
        if self._policy is FailPolicy.FAIL_OPEN:
            if entry is not None:
                # 有过期数据：按过期数据临时放行/拒绝，并明确标记 stale
                self.stats.stale_serves += 1
                return self._from_entry(serial, entry, now, stale=True,
                                        reason="source_down_stale_fail_open")
            return Result(serial=serial, decision=Decision.ALLOW, status=None,
                          reason="source_down_fail_open", from_cache=False,
                          stale=False, age=0.0)
        # FAIL_CLOSED：无论是否有过期数据，一律保守拒绝
        return Result(
            serial=serial,
            decision=Decision.DENY,
            status=entry.status if entry is not None else None,
            reason="source_down_fail_closed",
            from_cache=False,
            stale=entry is not None,
            age=(now - entry.fetched_at) if entry is not None else 0.0,
        )

    def _store(self, serial: str, status: Status, now: float) -> None:
        self._entries[serial] = _Entry(
            status=status, fetched_at=now, expires_at=now + self._ttl
        )
        self._entries.move_to_end(serial)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
            self.stats.evictions += 1
