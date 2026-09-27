"""示例协议：停等 ARQ（超时重传 + 序号 + 累积确认）。

在 netem 注入的丢包/乱序/重复链路上，保证数据按序、不丢、不重地交付。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from netem import Channel, Simulator


@dataclass(frozen=True)
class DataMsg:
    seq: int
    payload: Any


@dataclass(frozen=True)
class AckMsg:
    seq: int  # 已按序收到的最大序号


class StopAndWaitSender:
    def __init__(self, sim: Simulator, data_ch: Channel, payloads: list,
                 timeout: float = 0.4, size: int = 1000,
                 on_done: Callable[[], None] | None = None) -> None:
        self.sim = sim
        self.data_ch = data_ch
        self.payloads = list(payloads)
        self.timeout = timeout
        self.size = size
        self.on_done = on_done
        self.next_seq = 0        # 下一个待确认的序号
        self.attempt = 0         # 当前报文的发送尝试代次（用于忽略过期定时器）
        self.retransmissions = 0
        self.done = False

    def start(self) -> None:
        if self.payloads:
            self._send_current()
        else:
            self.done = True
            if self.on_done:
                self.on_done()

    def _send_current(self) -> None:
        seq = self.next_seq
        self.data_ch.send(DataMsg(seq, self.payloads[seq]), size=self.size)
        attempt = self.attempt
        self.sim.schedule(self.timeout, lambda: self._maybe_timeout(seq, attempt))

    def _maybe_timeout(self, seq: int, attempt: int) -> None:
        # 仅当该报文仍未确认且定时器未过期（代次一致）时重传
        if not self.done and seq == self.next_seq and attempt == self.attempt:
            self.attempt += 1
            self.retransmissions += 1
            self._send_current()

    def on_ack(self, msg: AckMsg) -> None:
        # 重复/乱序 ACK 自然被序号检查过滤
        if not self.done and msg.seq == self.next_seq:
            self.next_seq += 1
            self.attempt = 0
            if self.next_seq < len(self.payloads):
                self._send_current()
            else:
                self.done = True
                if self.on_done:
                    self.on_done()


class Receiver:
    """按序交付；对重复/乱序数据重发最近 ACK（应对 ACK 丢失）。"""

    def __init__(self, sim: Simulator, ack_ch: Channel, ack_size: int = 64) -> None:
        self.sim = sim
        self.ack_ch = ack_ch
        self.ack_size = ack_size
        self.expected = 0
        self.received: list = []

    def on_data(self, msg: DataMsg) -> None:
        if msg.seq == self.expected:
            self.received.append(msg.payload)
            self.expected += 1
        # seq < expected：重复包（ACK 丢了），需重发 ACK
        # seq > expected：窗口外，忽略数据但仍告知对方当前进度
        if self.expected > 0:
            self.ack_ch.send(AckMsg(self.expected - 1), size=self.ack_size)


def build_transfer(seed: int, segments_data, segments_ack, payloads,
                   timeout: float = 0.4, size: int = 1000):
    """搭好一对双向通道 + 发送方/接收方，返回 (sim, sender, receiver, ch_ab, ch_ba)。"""
    sim = Simulator()
    ch_ab = Channel(sim, "A->B(data)", segments_data, seed=seed)
    ch_ba = Channel(sim, "B->A(ack)", segments_ack, seed=seed)
    sender = StopAndWaitSender(sim, ch_ab, payloads, timeout=timeout, size=size)
    receiver = Receiver(sim, ch_ba)
    ch_ab.connect(receiver.on_data)
    ch_ba.connect(sender.on_ack)
    return sim, sender, receiver, ch_ab, ch_ba
