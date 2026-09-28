"""临时对象存储：读取路径判定可见性，清理按批执行且单批工作量有上界。

设计概述
========

数据结构（均由同一把可重入锁 ``_lock`` 保护）：

* ``_entries``: key -> _Entry，保存值、过期时刻、版本号；物理删除才会移除。
* ``_heap``: 最小堆，元素为 (expires_at, seq, version, key)。续期/覆写时不
  删除旧堆项，而是压入版本号更大的新堆项；旧堆项在清理时按版本号惰性丢弃。
* ``_pins``: key -> 进行中的“读取/续期”计数。被 pin 的对象即使已过期，
  清理也只跳过并把其堆项重新插回，等引用结束后的下一批再回收。

可见性
------
``get`` / ``__contains__`` / ``begin_access`` / ``renew`` 都在锁内比较
``expires_at`` 与当前时钟：过期对象立即不可见（即使物理条目还在）。

清理工作量
----------
``sweep_batch(budget)`` 每批最多弹堆 ``budget`` 次（工作量上界 = budget）。
堆顶未过期则提前停止，避免全量扫描。
"""

from __future__ import annotations

import heapq
import sys
import threading
import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Callable, Iterator, Optional, Type

Clock = Callable[[], float]


class MonotonicClock:
    """基于 time.monotonic_ns 的默认时钟，单位：纳秒（天然防时钟回拨）。"""

    def __call__(self) -> int:
        return time.monotonic_ns()


class FakeClock:
    """可控时钟，供测试使用；支持手动前进与回拨。"""

    def __init__(self, now: float = 0) -> None:
        self._now = now
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, delta: float) -> float:
        with self._lock:
            self._now += delta
            return self._now

    def set(self, now: float) -> None:
        with self._lock:
            self._now = now

    def rollback(self, delta: float) -> float:
        return self.advance(-delta)


@dataclass
class _Entry:
    value: Any
    expires_at: float
    version: int
    size: int


@dataclass
class SweepResult:
    """单次清理批次的工作量与回收统计。"""

    inspected: int = 0          # 本批弹堆次数（= 实际工作量，<= budget）
    reclaimed: int = 0          # 本批物理删除的对象数
    reclaimed_bytes: int = 0    # 本批回收的逻辑字节数
    skipped_not_expired: int = 0  # 尚未到期而保留的对象（触发本批提前停止，最多 1 个）
    skipped_pinned: int = 0     # 已过期但正被读取/续期而跳过
    stale_heap_items: int = 0   # 丢弃的过期/旧版本堆项（墓碑）
    early_stop: bool = False    # 是否因堆顶未过期而提前结束
    blocked_pinned: bool = False  # 是否因剩余最早过期对象全被 pin 而结束
    now: float = 0.0


@dataclass
class Stats:
    live_objects: int = 0           # 物理存在的对象数（含过期未清理）
    visible_objects: int = 0        # 当前时刻仍可见的对象数
    heap_items: int = 0             # 堆长度（含旧版本墓碑）
    live_bytes: int = 0             # 物理存在对象的逻辑字节合计
    pinned_objects: int = 0
    total_batches: int = 0
    total_inspected: int = 0
    total_reclaimed: int = 0
    total_reclaimed_bytes: int = 0
    total_skipped_pinned: int = 0
    total_stale_heap_items: int = 0


class KeyExpiredError(KeyError):
    """逻辑上已过期的键（物理条目可能仍在，等待清理）。"""


class _PinnedAccess:
    """begin_access 返回的上下文管理器：持有期间该对象不会被物理清理。"""

    def __init__(self, store: "LifecycleStore", key: Any, entry: _Entry) -> None:
        self._store = store
        self._key = key
        self._entry = entry
        self.value = entry.value

    def __enter__(self) -> "_PinnedAccess":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self._store._unpin(self._key)


class LifecycleStore:
    def __init__(self, clock: Optional[Clock] = None) -> None:
        self._clock: Clock = clock if clock is not None else MonotonicClock()
        self._lock = threading.RLock()
        self._entries: dict[Any, _Entry] = {}
        self._heap: list[tuple[float, int, int, Any]] = []
        self._pins: dict[Any, int] = {}
        self._seq = 0
        self._live_bytes = 0
        # 累计统计
        self._total_batches = 0
        self._total_inspected = 0
        self._total_reclaimed = 0
        self._total_reclaimed_bytes = 0
        self._total_skipped_pinned = 0
        self._total_stale_heap_items = 0
        # 测试钩子：对象被物理删除时回调，回调期间仍持锁。
        # 可用于断言“被删除对象当时没有任何进行中的读取/续期”。
        self.on_reclaim: Optional[Callable[[Any], None]] = None

    # ---------------------------------------------------------------- 基础操作

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def put(self, key: Any, value: Any, ttl: float, size: Optional[int] = None) -> float:
        """写入/覆写对象，返回过期时刻（时钟单位）。ttl <= 0 视为立即过期。"""
        if ttl is None or ttl < 0:
            raise ValueError("ttl must be >= 0")
        now = self._clock()
        expires_at = now + ttl
        if size is None:
            size = sys.getsizeof(value)
        with self._lock:
            old = self._entries.get(key)
            version = old.version + 1 if old is not None else 1
            entry = _Entry(value=value, expires_at=expires_at, version=version, size=size)
            self._entries[key] = entry
            self._live_bytes += size - (old.size if old is not None else 0)
            heapq.heappush(self._heap, (expires_at, self._next_seq(), version, key))
            return expires_at

    def get(self, key: Any, default: Any = None) -> Any:
        """读取；对象不存在或已过期时返回 default。过期对象立即不可见。"""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.expires_at <= self._clock():
                return default
            return entry.value

    def __contains__(self, key: Any) -> bool:
        with self._lock:
            entry = self._entries.get(key)
            return entry is not None and entry.expires_at > self._clock()

    def begin_access(self, key: Any) -> _PinnedAccess:
        """开始一次受保护的读取。

        成功则 pin 住对象：在 with 块结束前，即使对象过期，清理线程也只会
        跳过它。键不存在或已过期时抛 KeyError / KeyExpiredError。
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                raise KeyError(key)
            now = self._clock()
            if entry.expires_at <= now:
                raise KeyExpiredError(key)
            self._pins[key] = self._pins.get(key, 0) + 1
            return _PinnedAccess(self, key, entry)

    def renew(
        self,
        key: Any,
        ttl: float,
        _stall: Optional[Callable[[], None]] = None,
    ) -> float:
        """续期。返回新的过期时刻。

        语义与读取一致：调用瞬间键必须存在且未过期。内部通过 pin 保证：
        一旦续期开始（通过可见性检查），并发清理绝不会在续期完成前删除该
        对象。``_stall`` 仅供测试在“已通过可见性检查、尚未提交新过期时刻”
        的临界点注入调度，制造与清理线程的竞争。
        """
        if ttl is None or ttl < 0:
            raise ValueError("ttl must be >= 0")
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                raise KeyError(key)
            now = self._clock()
            if entry.expires_at <= now:
                raise KeyExpiredError(key)
            # 进入“续期中”状态：清理必须跳过。
            self._pins[key] = self._pins.get(key, 0) + 1
        try:
            if _stall is not None:
                # 在锁外注入停顿：此时别的线程可以运行清理批次。
                _stall()
            with self._lock:
                # pin 保证条目仍在；续期生成新版本，旧过期时刻的堆项
                # 在清理时按版本不匹配被惰性丢弃，绝不能误指向新对象。
                entry = self._entries[key]
                new_expires = self._clock() + ttl
                entry.expires_at = new_expires
                entry.version += 1
                heapq.heappush(
                    self._heap,
                    (new_expires, self._next_seq(), entry.version, key),
                )
                return new_expires
        finally:
            self._unpin(key)

    def delete(self, key: Any) -> bool:
        """主动删除；返回是否物理删除了一个存在的对象。"""
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                return False
            self._live_bytes -= entry.size
            # 堆项成为墓碑（版本不匹配），由后续清理批次惰性丢弃。
            return True

    def _unpin(self, key: Any) -> None:
        with self._lock:
            count = self._pins[key] - 1
            if count == 0:
                del self._pins[key]
            else:
                self._pins[key] = count

    # ---------------------------------------------------------------- 清理

    def sweep_batch(self, budget: int = 1000) -> SweepResult:
        """执行一批物理清理。

        工作量上界：最多从堆中弹出 ``budget`` 个元素（O(budget log n)）。
        遇到堆顶对象尚未过期则提前结束（``early_stop``），不做全量扫描。
        """
        if budget <= 0:
            raise ValueError("budget must be > 0")
        result = SweepResult(now=self._clock())
        # 本批已因 pinned 而重新插回的对象，避免同一对象在一批内重复计入。
        parked: set[Any] = set()
        with self._lock:
            now = self._clock()
            result.now = now
            heap = self._heap
            entries = self._entries
            pins = self._pins
            while result.inspected < budget and heap:
                expires_at, _seq, version, key = heapq.heappop(heap)
                result.inspected += 1
                if key in parked:
                    # 又绕回了本批先前 pin 的对象：它仍是最早的过期对象，
                    # 本批已无法继续推进，留待下一批（引用结束后）回收。
                    heapq.heappush(heap, (expires_at, _seq, version, key))
                    result.blocked_pinned = True
                    break
                entry = entries.get(key)
                if entry is None or entry.version != version:
                    # 对象已被删除，或该堆项属于旧版本（续期/覆写前的过期时间）。
                    result.stale_heap_items += 1
                    continue
                if expires_at > now:
                    # 堆顶最早过期的对象都还活着，后续对象必然更晚：提前停止。
                    heapq.heappush(heap, (expires_at, _seq, version, key))
                    result.skipped_not_expired += 1
                    result.early_stop = True
                    break
                if key in pins:
                    # 已过期但正被读取/续期：不得删除，重新插回本批之后处理。
                    heapq.heappush(heap, (expires_at, _seq, version, key))
                    parked.add(key)
                    result.skipped_pinned += 1
                    self._total_skipped_pinned += 1
                    continue
                # 提交物理删除。断言：走到这里的 key 不可能处于 pinned 状态。
                assert key not in pins, f"sweep attempted to reclaim pinned key {key!r}"
                del entries[key]
                self._live_bytes -= entry.size
                result.reclaimed += 1
                result.reclaimed_bytes += entry.size
                if self.on_reclaim is not None:
                    self.on_reclaim(key)
            self._total_batches += 1
            self._total_inspected += result.inspected
            self._total_reclaimed += result.reclaimed
            self._total_reclaimed_bytes += result.reclaimed_bytes
            self._total_stale_heap_items += result.stale_heap_items
            return result

    def sweep_drain(self, budget_per_batch: int = 1000) -> list[SweepResult]:
        """持续分批清理，直到某批提前停止（没有可回收对象）为止。"""
        results: list[SweepResult] = []
        while True:
            result = self.sweep_batch(budget_per_batch)
            results.append(result)
            if (
                result.early_stop
                or result.blocked_pinned
                or result.inspected < budget_per_batch
            ):
                break
        return results

    # ---------------------------------------------------------------- 观测

    def stats(self) -> Stats:
        with self._lock:
            now = self._clock()
            visible = sum(
                1 for entry in self._entries.values() if entry.expires_at > now
            )
            return Stats(
                live_objects=len(self._entries),
                visible_objects=visible,
                heap_items=len(self._heap),
                live_bytes=self._live_bytes,
                pinned_objects=len(self._pins),
                total_batches=self._total_batches,
                total_inspected=self._total_inspected,
                total_reclaimed=self._total_reclaimed,
                total_reclaimed_bytes=self._total_reclaimed_bytes,
                total_skipped_pinned=self._total_skipped_pinned,
                total_stale_heap_items=self._total_stale_heap_items,
            )


class IntervalSweeper(threading.Thread):
    """后台清理线程：每隔 interval（与时钟同一时间单位；MonotonicClock 下为
    纳秒）执行一批 sweep_batch。"""

    def __init__(
        self,
        store: LifecycleStore,
        interval: float,
        budget: int = 1000,
        *,
        daemon: bool = True,
        name: str = "lifecycle-sweeper",
    ) -> None:
        super().__init__(name=name, daemon=daemon)
        self._store = store
        self._interval = interval
        self._budget = budget
        self._stop_event = threading.Event()
        self.last_result: Optional[SweepResult] = None

    def stop(self, timeout: Optional[float] = None) -> None:
        self._stop_event.set()
        self.join(timeout)

    def run(self) -> None:
        # interval 是“时钟单位”，默认 MonotonicClock 为纳秒；换算成 sleep 秒。
        if isinstance(self._store._clock, MonotonicClock):
            seconds = self._interval / 1e9
        else:
            seconds = self._interval
        while not self._stop_event.wait(seconds):
            self.last_result = self._store.sweep_batch(self._budget)
