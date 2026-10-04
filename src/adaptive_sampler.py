"""自适应采样库（仅标准库）。

核心思路：
  - 滑动窗口统计到达流量与异常流量；
  - 采样率 p = 剩余正常预算 / 正常流量，钳制在 [min_rate, 1]；
  - 异常链路（is_error=True）始终优先保留，不计入概率采样；
  - 对正常链路，用 trace_id 的哈希与 p 对应阈值比较，保证同一链路
    （同一 trace_id 的所有 span）采样决定一致；
  - 每秒硬上限（hard_cap）作为兜底：预算耗尽时正常链路全丢，
    异常链路仍保留。
"""

from __future__ import annotations

import hashlib
import math
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

_HASH_SPACE = 1 << 64


def _hash_trace(trace_id: str) -> int:
    digest = hashlib.blake2b(str(trace_id).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


@dataclass
class WindowStats:
    offered: int          # 窗口内到达链路数
    errors: int           # 窗口内异常链路数
    kept: int             # 窗口内保留链路数
    kept_errors: int      # 窗口内保留的异常链路数

    @property
    def kept_normal(self) -> int:
        return self.kept - self.kept_errors


class AdaptiveSampler:
    """根据实时流量与每秒预算自适应调整采样率的链路采样器。

    参数
    ----
    budget_per_sec:
        每秒期望保留的链路总数（含异常链路）。
    window_sec:
        估计流量的滑动窗口长度。
    min_sample_rate:
        正常链路采样率下限（避免流量极大时完全不可观测）。
    hard_cap_per_sec:
        每秒保留链路数硬上限；None 表示不额外限制。
    always_keep_errors:
        是否无条件保留异常链路（异常优先）。
    decision_cache_size:
        链路决定缓存上限。头采样语义下，一条链路的决定只在首个
        span 到达时做出一次，之后同 trace_id 的调用直接返回缓存
        结果（等价于决定随链路上下文传播），保证链级一致性。
        缓存满时按先进先出淘汰。
    clock:
        可注入的单调时钟，便于测试。
    """

    def __init__(
        self,
        budget_per_sec: float,
        window_sec: float = 5.0,
        min_sample_rate: float = 0.001,
        hard_cap_per_sec: float | None = None,
        always_keep_errors: bool = True,
        decision_cache_size: int = 65536,
        clock=time.monotonic,
    ) -> None:
        if budget_per_sec <= 0:
            raise ValueError("budget_per_sec 必须为正数")
        if window_sec <= 0:
            raise ValueError("window_sec 必须为正数")
        if not 0.0 <= min_sample_rate <= 1.0:
            raise ValueError("min_sample_rate 必须在 [0, 1]")

        self.budget_per_sec = float(budget_per_sec)
        self.window_sec = float(window_sec)
        self.min_sample_rate = float(min_sample_rate)
        self.hard_cap_per_sec = hard_cap_per_sec
        self.always_keep_errors = always_keep_errors

        self._clock = clock
        self._events: deque[tuple[float, bool, bool]] = deque()
        # 每秒一个保留计数桶： (秒序号, 当秒保留数, 当秒异常保留数)
        self._cap_sec: int | None = None
        self._cap_kept = 0
        self._cap_kept_errors = 0

        self._decision_cache_size = decision_cache_size
        self._decisions: OrderedDict[str, bool] = OrderedDict()
        self._current_rate = min_sample_rate
        self._last_p = 1.0

    # ---- 内部：窗口维护 -------------------------------------------------

    def _prune(self, now: float) -> None:
        horizon = now - self.window_sec
        events = self._events
        while events and events[0][0] < horizon:
            events.popleft()

    def _roll_cap_bucket(self, now: float) -> None:
        sec = int(now)
        if self._cap_sec != sec:
            self._cap_sec = sec
            self._cap_kept = 0
            self._cap_kept_errors = 0

    def _record(self, now: float, is_error: bool, kept: bool) -> None:
        self._events.append((now, is_error, kept))
        self._roll_cap_bucket(now)
        if kept:
            self._cap_kept += 1
            if is_error:
                self._cap_kept_errors += 1

    def stats(self, now: float | None = None) -> WindowStats:
        now = self._clock() if now is None else now
        self._prune(now)
        offered = len(self._events)
        errors = sum(1 for _, e, _ in self._events if e)
        kept = sum(1 for _, _, k in self._events if k)
        kept_errors = sum(1 for _, e, k in self._events if e and k)
        return WindowStats(offered, errors, kept, kept_errors)

    # ---- 采样率计算 -----------------------------------------------------

    def sample_rate(self, now: float | None = None) -> float:
        """返回当前对正常链路生效的采样率（异常链路恒为 1）。"""
        now = self._clock() if now is None else now
        stats = self.stats(now)
        offered_rate = stats.offered / self.window_sec
        error_rate = stats.errors / self.window_sec

        if offered_rate <= 0.0:
            return 1.0

        if self.always_keep_errors:
            # 异常链路先占用预算，剩余的分给正常链路。
            normal_budget = max(0.0, self.budget_per_sec - error_rate)
            normal_rate = max(0.0, offered_rate - error_rate)
        else:
            normal_budget = self.budget_per_sec
            normal_rate = offered_rate

        if normal_rate <= 0.0:
            p = 1.0
        else:
            p = normal_budget / normal_rate
        p = min(1.0, max(self.min_sample_rate, p))
        return p

    # ---- 采样决定 -------------------------------------------------------

    def should_sample(self, trace_id: str, is_error: bool = False,
                      now: float | None = None) -> bool:
        """决定某条链路是否保留。

        同一 trace_id 的决定只计算一次并缓存，之后所有 span 都拿到
        相同结果（链级一致性）；决定本身由 trace_id 哈希与采样率
        阈值比较得出，跨进程/跨实例也可复现。
        """
        trace_id = str(trace_id)
        cached = self._decisions.get(trace_id)
        if cached is not None:
            return cached

        now = self._clock() if now is None else now
        self._prune(now)
        self._roll_cap_bucket(now)

        if is_error and self.always_keep_errors:
            # 异常是最高优先级，即使硬上限已满也保留。
            self._record(now, is_error, True)
            self._cache_decision(trace_id, True)
            return True

        # 硬上限已耗尽：预算用尽，正常链路全部丢弃。
        if self.hard_cap_per_sec is not None and \
                self._cap_kept >= self.hard_cap_per_sec:
            self._record(now, is_error, False)
            self._cache_decision(trace_id, False)
            return False

        p = self.sample_rate(now)
        self._last_p = p
        self._current_rate = p
        keep = _hash_trace(trace_id) < int(p * _HASH_SPACE)
        self._record(now, is_error, keep)
        self._cache_decision(trace_id, keep)
        return keep

    def _cache_decision(self, trace_id: str, keep: bool) -> None:
        self._decisions[trace_id] = keep
        if len(self._decisions) > self._decision_cache_size:
            self._decisions.popitem(last=False)

    # ---- 便捷方法 -------------------------------------------------------

    def decide_span(self, span: dict, now: float | None = None) -> bool:
        """对 span 做采样决定；span 至少含 trace_id，可带 is_error。"""
        return self.should_sample(
            str(span["trace_id"]),
            is_error=bool(span.get("is_error", False)),
            now=now,
        )
