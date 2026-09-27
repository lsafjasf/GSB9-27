"""Example protocol: stop-and-wait ARQ running on top of the injector.

The sender transmits fixed-size chunks with a sequence bit, waits for an
acknowledgement, and retransmits on timeout.  The receiver reassembles the
payload, ignoring duplicates and delayed old packets.  There are no wall
clock sleeps: every timer is scheduled on the deterministic simulator, so
thousands of round trips finish instantly and reproducibly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .injector import DOWNLINK, NetworkInjector, Timer

DATA_SIZE = 1024
ACK_SIZE = 40
RTO = 0.35
MAX_ATTEMPTS_PER_CHUNK = 50


def split_payload(payload: bytes, chunk_size: int = DATA_SIZE) -> List[bytes]:
    return [payload[i : i + chunk_size] for i in range(0, len(payload), chunk_size)]


@dataclass
class ProtocolStats:
    data_sent: int = 0
    data_retransmits: int = 0
    ack_sent: int = 0
    timeouts: int = 0
    chunks_total: int = 0
    duplicate_data_received: int = 0
    stale_data_received: int = 0
    stale_acks_received: int = 0
    finish_time: float = 0.0
    attempts_per_chunk: List[int] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        attempts = self.attempts_per_chunk
        return {
            "data_sent": self.data_sent,
            "data_retransmits": self.data_retransmits,
            "ack_sent": self.ack_sent,
            "timeouts": self.timeouts,
            "chunks_total": self.chunks_total,
            "duplicate_data_received": self.duplicate_data_received,
            "stale_data_received": self.stale_data_received,
            "stale_acks_received": self.stale_acks_received,
            "finish_time": round(self.finish_time, 6),
            "max_attempts_for_one_chunk": max(attempts) if attempts else 0,
            "avg_attempts_per_chunk": round(sum(attempts) / len(attempts), 4) if attempts else 0.0,
        }


class StopAndWaitSender:
    def __init__(
        self,
        net: NetworkInjector,
        payload: bytes,
        on_complete: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.chunks = split_payload(payload)
        self.net = net
        self.on_complete = on_complete
        self.stats = ProtocolStats(chunks_total=len(self.chunks))
        self._index = 0
        self._attempts = 0
        self._timer = Timer(net.sim)

    def start(self) -> None:
        self._transmit()

    def _transmit(self) -> None:
        seq_bit = self._index % 2
        packet = (b"D", seq_bit, self._index, self.chunks[self._index])
        self.net.send_downlink(packet, size_bytes=DATA_SIZE)
        self.stats.data_sent += 1
        self._attempts += 1
        self._timer.start(RTO, self._on_timeout)

    def _on_timeout(self, time_s: float) -> None:
        self.stats.timeouts += 1
        if self._attempts >= MAX_ATTEMPTS_PER_CHUNK:
            raise RuntimeError(
                "chunk %d exceeded %d attempts; transfer cannot complete"
                % (self._index, MAX_ATTEMPTS_PER_CHUNK)
            )
        self.stats.data_retransmits += 1
        self._transmit()

    def receive_ack(self, time_s: float, seq_bit: int, chunk_index: int) -> None:
        expected_bit = self._index % 2
        if chunk_index != self._index or seq_bit != expected_bit:
            self.stats.stale_acks_received += 1
            return
        self._timer.cancel()
        self.stats.attempts_per_chunk.append(self._attempts)
        self._index += 1
        self._attempts = 0
        if self._index >= len(self.chunks):
            self.stats.finish_time = time_s
            if self.on_complete is not None:
                self.on_complete(time_s)
            return
        self._transmit()


class StopAndWaitReceiver:
    def __init__(self, net: NetworkInjector, sender: StopAndWaitSender) -> None:
        self.net = net
        self.sender = sender
        self.received: List[bytes] = []
        self._next_index = 0

    def receive_data(self, time_s: float, _seq: int, message: object) -> None:
        kind, seq_bit, chunk_index, data = message
        assert kind == b"D"
        if chunk_index < self._next_index:
            self.sender.stats.stale_data_received += 1
        elif chunk_index == self._next_index:
            if seq_bit != self._next_index % 2:
                self.sender.stats.duplicate_data_received += 1
            else:
                self.received.append(data)
                self._next_index += 1
        else:
            # A future chunk cannot happen in stop-and-wait; treat as stale.
            self.sender.stats.stale_data_received += 1
        # Always acknowledge the current request; duplicates get duplicate ACKs.
        ack = (b"A", chunk_index % 2, chunk_index)
        self.net.send_uplink(ack, size_bytes=ACK_SIZE)
        self.sender.stats.ack_sent += 1

    def payload(self) -> bytes:
        return b"".join(self.received)
