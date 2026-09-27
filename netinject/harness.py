"""Helpers for running the stop-and-wait protocol under injection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from .arq import StopAndWaitReceiver, StopAndWaitSender
from .injector import DOWNLINK, UPLINK, NetworkConfig, NetworkInjector


@dataclass
class TransferResult:
    payload: bytes
    received: bytes
    md5_ok: bool
    protocol: dict
    network: dict
    finish_time: float


def run_transfer(
    payload: bytes,
    config: NetworkConfig,
    seed: int,
    run_until: Optional[float] = None,
) -> TransferResult:
    net_holder: dict = {}

    def on_downlink(time_s, seq, message):
        net_holder["receiver"].receive_data(time_s, seq, message)

    def on_uplink(time_s, seq, message):
        net_holder["sender"].receive_ack(time_s, message[1], message[2])

    net = NetworkInjector(config, seed=seed, on_downlink=on_downlink, on_uplink=on_uplink)
    sender = StopAndWaitSender(net, payload)
    receiver = StopAndWaitReceiver(net, sender)
    net_holder["sender"] = sender
    net_holder["receiver"] = receiver
    sender.start()
    net.run(until=run_until)

    received = receiver.payload()
    return TransferResult(
        payload=payload,
        received=received,
        md5_ok=hashlib.md5(received).digest() == hashlib.md5(payload).digest()
        and received == payload,
        protocol=sender.stats.as_dict(),
        network=net.stats_summary(),
        finish_time=sender.stats.finish_time,
    )


def format_network_table(result: TransferResult) -> str:
    cols = (
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
    lines = ["direction    " + " ".join("%10s" % label for _, label in cols)]
    for direction in (DOWNLINK, UPLINK):
        data = result.network[direction]
        lines.append(
            "%-12s " % direction
            + " ".join("%10s" % _cell(data[key]) for key, _ in cols)
        )
    return "\n".join(lines)


def _cell(value):
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return "%.4f" % value
