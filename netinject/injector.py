"""Network behavior injector built on a deterministic discrete-event clock.

Only the Python standard library is used.  Every random decision is drawn
from a seeded ``random.Random`` instance with a fixed draw order, so that
the same seed and the same send() sequence always produce the same result.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

DOWNLINK = "downlink"
UPLINK = "uplink"
DIRECTIONS = (DOWNLINK, UPLINK)

DeliverFn = Callable[[float, int, object], None]


def _check_fraction(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError("%s must be in [0, 1], got %r" % (name, value))


def _check_nonneg(name: str, value: float) -> None:
    if value < 0:
        raise ValueError("%s must be >= 0, got %r" % (name, value))


@dataclass(frozen=True)
class Profile:
    """Link parameters active during one time segment.

    loss/reorder/duplicate are independent Bernoulli probabilities.
    base_delay/jitter are in seconds; jitter is sampled uniformly from
    [-jitter, +jitter] and always drawn (even when jitter == 0) so that
    changing jitter cannot perturb later random decisions.
    bandwidth_bps == 0 means unlimited bandwidth.
    """

    base_delay: float = 0.0
    jitter: float = 0.0
    loss: float = 0.0
    reorder: float = 0.0
    duplicate: float = 0.0
    reorder_extra_delay: float = 0.0
    bandwidth_bps: float = 0.0

    def __post_init__(self) -> None:
        _check_fraction("loss", self.loss)
        _check_fraction("reorder", self.reorder)
        _check_fraction("duplicate", self.duplicate)
        _check_nonneg("base_delay", self.base_delay)
        _check_nonneg("jitter", self.jitter)
        _check_nonneg("reorder_extra_delay", self.reorder_extra_delay)
        _check_nonneg("bandwidth_bps", self.bandwidth_bps)


@dataclass(frozen=True)
class Segment:
    """A :class:`Profile` active starting at ``start`` seconds."""

    start: float
    profile: Profile

    def __post_init__(self) -> None:
        _check_nonneg("segment start", self.start)


class SegmentedProfile:
    """Piecewise-constant profile: parameters change at fixed times."""

    def __init__(self, segments: List[Segment]):
        if not segments:
            raise ValueError("at least one segment is required")
        ordered = sorted(segments, key=lambda seg: seg.start)
        if ordered[0].start != 0.0:
            raise ValueError("first segment must start at 0.0")
        self._segments = ordered

    @staticmethod
    def constant(profile: Profile) -> "SegmentedProfile":
        return SegmentedProfile([Segment(0.0, profile)])

    def at(self, time_s: float) -> Profile:
        active = self._segments[0].profile
        for segment in self._segments:
            if time_s >= segment.start:
                active = segment.profile
            else:
                break
        return active


@dataclass
class NetworkConfig:
    """Per-direction configuration.

    A single profile is applied symmetrically; otherwise supply one profile
    per direction.
    """

    downlink: SegmentedProfile
    uplink: Optional[SegmentedProfile] = None

    @staticmethod
    def symmetric(profile: SegmentedProfile) -> "NetworkConfig":
        return NetworkConfig(downlink=profile, uplink=profile)


@dataclass(order=True)
class _Event:
    time: float
    seq: int
    callback: Callable[[float], None] = field(compare=False)
    cancelled: bool = field(default=False, compare=False)


class Simulator:
    """Ministure deterministic discrete-event scheduler."""

    def __init__(self) -> None:
        self.time = 0.0
        self._events: List[_Event] = []
        self._counter = 0

    def schedule(self, delay: float, callback: Callable[[float], None]) -> _Event:
        if delay < 0:
            raise ValueError("cannot schedule an event in the past")
        self._counter += 1
        event = _Event(self.time + delay, self._counter, callback)
        self._events.append(event)
        return event

    def schedule_at(self, time_s: float, callback: Callable[[float], None]) -> _Event:
        self._counter += 1
        event = _Event(time_s, self._counter, callback)
        self._events.append(event)
        return event

    def run(self, until: Optional[float] = None) -> None:
        while self._events:
            ready = min(self._events)
            if until is not None and ready.time > until:
                break
            self._events.remove(ready)
            if ready.cancelled:
                continue
            self.time = ready.time
            ready.callback(ready.time)
            self._events = [event for event in self._events if not event.cancelled]

    def pending(self) -> int:
        return sum(1 for event in self._events if not event.cancelled)


class Timer:
    """A cancellable one-shot timer on top of the simulator."""

    def __init__(self, sim: Simulator):
        self.sim = sim
        self._event: Optional[_Event] = None

    def start(self, delay: float, callback: Callable[[float], None]) -> None:
        self.cancel()
        self._event = self.sim.schedule(delay, callback)

    def cancel(self) -> None:
        if self._event is not None:
            self._event.cancelled = True
            self._event = None


def percentile(sorted_values: List[float], pct: float) -> float:
    """Nearest-rank percentile of an already sorted, non-empty list."""
    if not sorted_values:
        raise ValueError("percentile requires at least one value")
    rank = pct / 100.0 * len(sorted_values)
    index = int(rank)
    if rank != index:
        index += 1
    index = max(1, min(index, len(sorted_values)))
    return sorted_values[index - 1]


@dataclass
class LinkStats:
    direction: str
    sent: int = 0
    lost: int = 0
    delivered: int = 0
    duplicate_deliveries: int = 0
    reorders: int = 0
    _latencies: List[float] = field(default_factory=list)
    _max_inflight: int = 0

    def record_latency(self, value: float) -> None:
        self._latencies.append(value)

    @property
    def latencies(self) -> List[float]:
        return list(self._latencies)

    @property
    def loss_rate(self) -> float:
        return self.lost / self.sent if self.sent else 0.0

    @property
    def avg_delay(self) -> float:
        return sum(self._latencies) / len(self._latencies) if self._latencies else 0.0

    @property
    def p50_delay(self) -> float:
        return percentile(sorted(self._latencies), 50) if self._latencies else 0.0

    @property
    def p95_delay(self) -> float:
        return percentile(sorted(self._latencies), 95) if self._latencies else 0.0

    @property
    def p99_delay(self) -> float:
        return percentile(sorted(self._latencies), 99) if self._latencies else 0.0

    @property
    def max_delay(self) -> float:
        return max(self._latencies) if self._latencies else 0.0

    def as_dict(self) -> Dict[str, float]:
        return {
            "direction": self.direction,
            "sent": self.sent,
            "lost": self.lost,
            "loss_rate": round(self.loss_rate, 6),
            "delivered": self.delivered,
            "duplicate_deliveries": self.duplicate_deliveries,
            "reorders": self.reorders,
            "avg_delay": round(self.avg_delay, 6),
            "p50_delay": round(self.p50_delay, 6),
            "p95_delay": round(self.p95_delay, 6),
            "p99_delay": round(self.p99_delay, 6),
            "max_delay": round(self.max_delay, 6),
        }


class _Link:
    """One directional unreliable link: loss / latency / reorder / dup / BW."""

    def __init__(
        self,
        direction: str,
        config: SegmentedProfile,
        sim: Simulator,
        rng: random.Random,
        deliver: DeliverFn,
    ) -> None:
        self.direction = direction
        self.config = config
        self.sim = sim
        self.rng = rng
        self._deliver = deliver
        self.stats = LinkStats(direction)
        self._seq = 0
        self._delivered_seqs = set()
        self._delivered_order: List[int] = []
        self._last_delivered_seq = -1
        self._serializer_free_at = 0.0

    def send(self, message: object, size_bytes: Optional[int]) -> None:
        now = self.sim.time
        profile = self.config.at(now)
        seq = self._seq
        self._seq += 1
        self.stats.sent += 1

        # Fixed draw order for every message: loss, duplicate, jitter, reorder.
        drop = self.rng.random() < profile.loss
        duplicated = self.rng.random() < profile.duplicate
        jitter = self.rng.uniform(-profile.jitter, profile.jitter)
        reordered = self.rng.random() < profile.reorder

        if drop:
            self.stats.lost += 1
            return

        delay = max(0.0, profile.base_delay + jitter)
        if reordered:
            delay += profile.reorder_extra_delay

        size = len(message) if size_bytes is None else size_bytes
        tx_time = size * 8.0 / profile.bandwidth_bps if profile.bandwidth_bps > 0 and size > 0 else 0.0

        copies = 2 if duplicated else 1
        if duplicated:
            self.stats.duplicate_deliveries += 1

        # All copies of one message serialize onto the same emulated wire,
        # so a duplicate is delivered immediately after its original.
        for _ in range(copies):
            tx_start = max(now, self._serializer_free_at)
            self._serializer_free_at = tx_start + tx_time
            queue_delay = tx_start - now
            arrival = tx_start + tx_time + delay
            self.sim.schedule_at(
                arrival, self._make_arrival(seq, message, queue_delay + tx_time + delay)
            )

    def _make_arrival(
        self, seq: int, message: object, propagation_delay: float
    ) -> Callable[[float], None]:
        def on_arrival(arrival_time: float) -> None:
            first_delivery = seq not in self._delivered_seqs
            if first_delivery:
                self._delivered_seqs.add(seq)
                self.stats.delivered += 1
                self.stats.record_latency(propagation_delay)
                if seq < self._last_delivered_seq:
                    self.stats.reorders += 1
                self._last_delivered_seq = max(self._last_delivered_seq, seq)
            self._delivered_order.append(seq)
            self._deliver(arrival_time, seq, message)

        return on_arrival


class NetworkInjector:
    """Bidirectional seeded network injector.

    Parameters
    ----------
    config:
        Per-direction segmented profiles.
    seed:
        Integer seed.  The two directions use independent derived streams so
        that changing traffic on one direction cannot change the draws of the
        other.
    on_downlink, on_uplink:
        ``fn(time_s, seq, message)`` delivery callbacks.
    """

    def __init__(
        self,
        config: NetworkConfig,
        seed: int,
        on_downlink: DeliverFn,
        on_uplink: Optional[DeliverFn] = None,
    ) -> None:
        self.sim = Simulator()
        rng = random.Random(seed)
        down_rng = random.Random(rng.getrandbits(128))
        up_rng = random.Random(rng.getrandbits(128))
        uplink_config = config.uplink or config.downlink
        self._links: Dict[str, _Link] = {
            DOWNLINK: _Link(DOWNLINK, config.downlink, self.sim, down_rng, on_downlink),
            UPLINK: _Link(UPLINK, uplink_config, self.sim, up_rng, on_uplink or (lambda t, s, m: None)),
        }

    def send_downlink(self, message: object, size_bytes: Optional[int] = None) -> None:
        self._links[DOWNLINK].send(message, size_bytes)

    def send_uplink(self, message: object, size_bytes: Optional[int] = None) -> None:
        self._links[UPLINK].send(message, size_bytes)

    def send(self, direction: str, message: object, size_bytes: Optional[int] = None) -> None:
        self._links[direction].send(message, size_bytes)

    def run(self, until: Optional[float] = None) -> None:
        self.sim.run(until)

    @property
    def time(self) -> float:
        return self.sim.time

    def stats(self, direction: str) -> LinkStats:
        return self._links[direction].stats

    def stats_summary(self) -> Dict[str, Dict[str, float]]:
        return {direction: self._links[direction].stats.as_dict() for direction in DIRECTIONS}

    def format_stats(self) -> str:
        columns = (
            ("sent", "sent"),
            ("lost", "lost"),
            ("loss_rate", "loss%"),
            ("delivered", "delivered"),
            ("duplicate_deliveries", "dups"),
            ("reorders", "reorders"),
            ("avg_delay", "avg(s)"),
            ("p95_delay", "p95(s)"),
            ("p99_delay", "p99(s)"),
            ("max_delay", "max(s)"),
        )
        header = "direction   " + " ".join("%10s" % label for _, label in columns)
        lines = [header]
        for direction in DIRECTIONS:
            data = self._links[direction].stats.as_dict()
            row = "%-11s " % direction
            row += " ".join("%10s" % _format_cell(data[key]) for key, _ in columns)
            lines.append(row)
        return "\n".join(lines)


def _format_cell(value: float) -> str:
    if isinstance(value, int) or value == int(value):
        return str(int(value))
    return "%.4f" % value
