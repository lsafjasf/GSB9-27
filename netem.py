"""netem — 网络行为注入器（纯标准库，离散事件仿真）。

按方向注入：延迟 / 抖动 / 丢包 / 乱序 / 重复 / 带宽限制。
参数按时间分段配置；所有随机性由种子驱动，结果完全可复现。
"""
from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field

INF = float("inf")


class Simulator:
    """离散事件仿真器：虚拟时间单调推进，事件按 (时间, 序号) 出堆。"""

    def __init__(self) -> None:
        self._queue: list = []
        self._seq = 0
        self.now = 0.0

    def schedule(self, delay: float, fn) -> None:
        self.call_at(self.now + delay, fn)

    def call_at(self, t: float, fn) -> None:
        heapq.heappush(self._queue, (t, self._seq, fn))
        self._seq += 1

    def run(self, until: float | None = None) -> None:
        while self._queue:
            t, _, fn = self._queue[0]
            if until is not None and t > until:
                self.now = until
                return
            heapq.heappop(self._queue)
            self.now = t
            fn()


@dataclass(frozen=True)
class LinkParams:
    """单方向链路的注入参数。"""
    loss_rate: float = 0.0        # 丢包概率 [0,1]
    delay: float = 0.01           # 基础单向时延（秒）
    jitter: float = 0.0           # 附加均匀抖动 U(0, jitter)
    reorder_rate: float = 0.0     # 乱序注入概率
    reorder_delay: float = 0.05   # 乱序时追加的额外时延
    duplicate_rate: float = 0.0   # 重复包概率
    bandwidth: float | None = None  # 带宽上限（字节/秒），None 表示不限


@dataclass(frozen=True)
class Segment:
    """时间分段：[start, end) 内使用 params。"""
    start: float
    params: LinkParams
    end: float = INF


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


@dataclass
class ChannelStats:
    sent: int = 0                  # 进入通道的报文数（含协议重传）
    dropped: int = 0               # 被注入丢弃
    duplicates_injected: int = 0   # 注入产生的重复副本
    delivered: int = 0             # 首次送达（按发送序号去重）
    duplicate_deliveries: int = 0  # 重复副本送达次数
    reordered: int = 0             # 实际乱序送达（到达顺序落后于更晚发送的报文）
    delays: list[float] = field(default_factory=list)  # 首次送达的端到端时延

    def snapshot(self) -> tuple:
        """可哈希快照，用于可复现性断言。"""
        return (self.sent, self.dropped, self.duplicates_injected,
                self.delivered, self.duplicate_deliveries, self.reordered,
                tuple(self.delays))

    def summary(self) -> dict:
        d = sorted(self.delays)
        avg = sum(d) / len(d) if d else 0.0
        return {
            "sent": self.sent,
            "dropped": self.dropped,
            "loss_rate_actual": self.dropped / self.sent if self.sent else 0.0,
            "duplicates_injected": self.duplicates_injected,
            "delivered": self.delivered,
            "duplicate_deliveries": self.duplicate_deliveries,
            "reordered": self.reordered,
            "delay_avg": avg,
            "delay_p50": _percentile(d, 0.50),
            "delay_p95": _percentile(d, 0.95),
            "delay_p99": _percentile(d, 0.99),
            "delay_max": d[-1] if d else 0.0,
        }


def format_stats(name: str, stats: ChannelStats) -> str:
    s = stats.summary()
    return (
        f"--- 通道 {name} ---\n"
        f"  发送 {s['sent']}  丢弃 {s['dropped']} ({s['loss_rate_actual']:.1%})  "
        f"注入重复 {s['duplicates_injected']}\n"
        f"  送达 {s['delivered']}  重复送达 {s['duplicate_deliveries']}  "
        f"乱序 {s['reordered']}\n"
        f"  时延 avg {s['delay_avg']*1e3:.1f}ms  p50 {s['delay_p50']*1e3:.1f}ms  "
        f"p95 {s['delay_p95']*1e3:.1f}ms  p99 {s['delay_p99']*1e3:.1f}ms  "
        f"max {s['delay_max']*1e3:.1f}ms"
    )


class Channel:
    """单向链路。send() 时按当前时间段参数决定丢包/重复/乱序/时延/带宽排队。"""

    def __init__(self, sim: Simulator, name: str,
                 segments: list[Segment] | None = None, seed: int = 0) -> None:
        self.sim = sim
        self.name = name
        self.segments = sorted(segments or [Segment(0.0, LinkParams())],
                               key=lambda s: s.start)
        # 用字符串种子避免 PYTHONHASHSEED 的影响，保证跨进程可复现
        self.rng = random.Random(f"netem|{seed}|{name}")
        self.stats = ChannelStats()
        self._sink = None
        self._busy_until = 0.0        # 带宽串行化：链路空闲时刻
        self._send_index = 0          # 发送序号（通道局部）
        self._max_index = -1          # 已首次送达的最大发送序号
        self._seen: set[int] = set()

    def connect(self, sink) -> None:
        self._sink = sink

    def params_at(self, t: float) -> LinkParams:
        for seg in self.segments:
            if seg.start <= t < seg.end:
                return seg.params
        return LinkParams()

    def send(self, msg, size: int = 1) -> None:
        p = self.params_at(self.sim.now)
        idx = self._send_index
        self._send_index += 1
        self.stats.sent += 1
        t0 = self.sim.now

        if self.rng.random() < p.loss_rate:
            self.stats.dropped += 1
            return

        copies = 1
        if self.rng.random() < p.duplicate_rate:
            copies = 2
            self.stats.duplicates_injected += 1
        for c in range(copies):
            self._transmit(msg, size, idx, t0, p)

    def _transmit(self, msg, size: int, idx: int, t0: float, p: LinkParams) -> None:
        # 带宽限制：报文串行占用链路 size/bandwidth 秒
        if p.bandwidth:
            start = max(self.sim.now, self._busy_until)
            depart = start + size / p.bandwidth
            self._busy_until = depart
        else:
            depart = self.sim.now

        d = p.delay + (self.rng.uniform(0.0, p.jitter) if p.jitter else 0.0)
        if p.reorder_rate and self.rng.random() < p.reorder_rate:
            d += p.reorder_delay  # 注入乱序：额外滞留使其被后续报文超越

        arrive = depart + d
        self.sim.call_at(arrive, lambda: self._deliver(msg, idx, t0))

    def _deliver(self, msg, idx: int, t0: float) -> None:
        if idx in self._seen:
            self.stats.duplicate_deliveries += 1
        else:
            self._seen.add(idx)
            if idx < self._max_index:
                self.stats.reordered += 1
            self._max_index = max(self._max_index, idx)
            self.stats.delivered += 1
            self.stats.delays.append(self.sim.now - t0)
        if self._sink is not None:
            self._sink(msg)
