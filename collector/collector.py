"""采集器：在线直发 / 离线落盘 / 恢复后限速重发。

不重复上报的保证：
  1. 每条记录携带单调递增 id；
  2. 缓冲游标只在 transport 明确返回“已接受 n 条”后推进 n，
     部分接受只确认部分，失败（抛异常）一条都不确认；
  3. 缓冲非空时新数据直接落盘排队，不插队直发，保证顺序与恰好一次。
"""

from typing import Any, Callable, Dict, List, Optional, Protocol

from .buffer import DiskBuffer
from .clock import Clock, SystemClock


class Transport(Protocol):
    def send(self, batch: List[Dict[str, Any]]) -> int:
        """发送一批记录，返回服务端实际接受的条数；断网应抛异常。"""


class ResendMetrics:
    """一次重发过程的采样数据：速率、积压下降曲线。"""

    def __init__(self, start_time: float, start_pending: int) -> None:
        self.start_time = start_time
        self.start_pending = start_pending
        self.end_time: Optional[float] = None
        self.sent = 0
        # (相对秒, 剩余积压) 采样点，用于观察积压下降。
        self.samples: List[tuple] = [(0.0, start_pending)]

    def sample(self, now: float, pending: int) -> None:
        self.samples.append((round(now - self.start_time, 6), pending))

    def finish(self, now: float) -> None:
        self.end_time = now

    @property
    def elapsed(self) -> float:
        end = self.end_time if self.end_time is not None else self.start_time
        return end - self.start_time

    @property
    def effective_rate(self) -> float:
        """实际重发速率（条/秒）。"""
        if self.elapsed <= 0:
            return float("inf") if self.sent else 0.0
        return self.sent / self.elapsed


class Collector:
    def __init__(
        self,
        buffer: DiskBuffer,
        transport: Transport,
        clock: Optional[Clock] = None,
        resend_rate: float = 50.0,
        batch_size: int = 50,
        record_id_start: int = 0,
    ) -> None:
        if resend_rate <= 0:
            raise ValueError("resend_rate 必须为正")
        if batch_size <= 0:
            raise ValueError("batch_size 必须为正")
        self._buffer = buffer
        self._transport = transport
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._resend_rate = float(resend_rate)
        self._batch_size = int(batch_size)
        self._seq = int(record_id_start)
        # 统计
        self.sent_online = 0
        self.resent = 0

    # ---- 采集入口 -----------------------------------------------------------
    def emit(self, payload: Any) -> bool:
        """采集一条数据。返回 True 表示已实时上报，False 表示已落盘缓冲。"""
        self._seq += 1
        record = {"id": self._seq, "ts": self._clock.now(), "payload": payload}
        if self._buffer.pending > 0:
            # 缓冲里还有积压：新数据排队，避免乱序与重复。
            self._buffer.append(record)
            return False
        try:
            accepted = self._transport.send([record])
        except Exception:
            accepted = 0
        if accepted == 1:
            self.sent_online += 1
            return True
        self._buffer.append(record)
        return False

    # ---- 重发 ---------------------------------------------------------------
    def flush_batch(self, max_n: Optional[int] = None) -> int:
        """尝试重发一批，返回成功条数（0 表示仍离线或缓冲为空）。"""
        n = self._batch_size if max_n is None else min(max_n, self._batch_size)
        batch = self._buffer.read_batch(n)
        if not batch:
            return 0
        try:
            accepted = self._transport.send(batch)
        except Exception:
            return 0
        accepted = max(0, min(accepted, len(batch)))
        if accepted:
            self._buffer.ack(accepted)
            self.resent += accepted
        return accepted

    def drain(self, on_progress: Optional[Callable[[ResendMetrics], None]] = None) -> ResendMetrics:
        """限速重发直到缓冲清空或再次断网。令牌桶限速，桶容量 = batch_size。"""
        metrics = ResendMetrics(self._clock.now(), self._buffer.pending)
        tokens = float(self._batch_size)  # 允许一次初始突发，之后按速率补充
        last = self._clock.now()
        while self._buffer.pending > 0:
            now = self._clock.now()
            tokens = min(float(self._batch_size), tokens + (now - last) * self._resend_rate)
            last = now
            if tokens < 1.0:
                # 睡眠“恰好凑满 1 个令牌”的时长后直接置 1.0：
                # 真实时钟睡够了这么久，置 1.0 是保守且正确的；
                # 同时避免浮点误差（如 0.03-0.02=0.00999...）令假时钟原地踏步死循环。
                self._clock.sleep((1.0 - tokens) / self._resend_rate)
                last = self._clock.now()
                tokens = 1.0
            sent = self.flush_batch(max_n=int(tokens + 1e-9))
            if sent == 0:
                break  # 仍然离线，保留积压，等待下一次 drain
            tokens -= sent
            metrics.sent += sent
            metrics.sample(self._clock.now(), self._buffer.pending)
            if on_progress is not None:
                on_progress(metrics)
        metrics.finish(self._clock.now())
        return metrics

    # ---- 状态 ---------------------------------------------------------------
    @property
    def backlog(self) -> int:
        return self._buffer.pending

    @property
    def dropped(self) -> int:
        return self._buffer.dropped
