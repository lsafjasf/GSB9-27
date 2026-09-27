#!/usr/bin/env python3
"""Demo: parameters change over time; print injection statistics.

Traffic model: 150 messages, one every 50 ms on the downlink starting at t=0.
The downlink profile changes at t=2s and t=4s.
"""

import json

from netinject import (
    DOWNLINK,
    UPLINK,
    NetworkConfig,
    NetworkInjector,
    Profile,
    Segment,
    SegmentedProfile,
)

SEED = 20260928
MESSAGE_COUNT = 150
INTERVAL = 0.05

downlink = SegmentedProfile([
    Segment(0.0, Profile(base_delay=0.05, jitter=0.01, loss=0.00)),
    Segment(2.0, Profile(base_delay=0.05, jitter=0.01, loss=0.20,
                         duplicate=0.05, reorder=0.10,
                         reorder_extra_delay=0.15)),
    Segment(4.0, Profile(base_delay=0.10, jitter=0.04, loss=0.05,
                         bandwidth_bps=64_000, reorder=0.05,
                         reorder_extra_delay=0.20)),
])
uplink = SegmentedProfile.constant(
    Profile(base_delay=0.02, jitter=0.005, loss=0.05)
)

received = []


def on_downlink(time_s, seq, message):
    received.append((time_s, seq, message))


def on_uplink(time_s, seq, message):
    received.append((time_s, seq, message))


def main():
    net = NetworkInjector(
        NetworkConfig(downlink=downlink, uplink=uplink),
        seed=SEED,
        on_downlink=on_downlink,
        on_uplink=on_uplink,
    )
    for index in range(MESSAGE_COUNT):
        net.sim.schedule(index * INTERVAL, lambda _t, i=index: net.send_downlink(b"m%04d" % i, 100))
    # A little uplink traffic too (e.g. acks), same cadence, half as many.
    for index in range(0, MESSAGE_COUNT, 2):
        net.sim.schedule(index * INTERVAL, lambda _t, i=index: net.send_uplink(b"a%04d" % i, 40))
    net.run()

    print("seed:", SEED)
    print("simulated duration: %.3f s" % net.time)
    print()
    print(net.format_stats())
    print()
    down = net.stats(DOWNLINK)
    inversions = sum(
        1 for i in range(1, len(received))
        if received[i][1] < received[i - 1][1] and received[i][2].startswith(b"m")
    )
    print("observed downlink seq inversions at receiver:", inversions,
          "(tracked reorders:", down.reorders, ")")
    print()
    print("json stats:")
    print(json.dumps(net.stats_summary(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
