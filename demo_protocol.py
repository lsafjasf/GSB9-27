#!/usr/bin/env python3
"""Demo: stop-and-wait ARQ completes a transfer under 10% packet loss.

Both directions lose 10% of packets (independent seeded streams).  Verifies
byte-for-byte integrity via MD5 and prints protocol + injection statistics.
"""

import hashlib
import json

from netinject import NetworkConfig, Profile, SegmentedProfile, format_network_table, run_transfer

SEED = 4242
PAYLOAD_SIZE = 32 * 1024


def deterministic_payload(size: int) -> bytes:
    data = bytearray(size)
    state = 0x1234_ABCD
    for index in range(size):
        state = (state * 1_103_515_245 + 12345) & 0xFFFF_FFFF
        data[index] = ((state >> 16) ^ index) & 0xFF
    return bytes(data)


def main():
    payload = deterministic_payload(PAYLOAD_SIZE)
    profile = SegmentedProfile.constant(
        Profile(base_delay=0.05, jitter=0.01, loss=0.10)
    )
    config = NetworkConfig(downlink=profile, uplink=SegmentedProfile.constant(
        Profile(base_delay=0.05, jitter=0.01, loss=0.10)
    ))

    result = run_transfer(payload, config, seed=SEED)

    print("seed:", SEED, " payload:", PAYLOAD_SIZE, "bytes")
    print("md5(payload)  =", hashlib.md5(payload).hexdigest())
    print("md5(received) =", hashlib.md5(result.received).hexdigest())
    print("integrity ok  :", result.md5_ok, " bytes:", len(result.received))
    print("finish time   : %.3f simulated seconds" % result.finish_time)
    print()
    print("injection statistics:")
    print(format_network_table(result))
    print()
    print("protocol statistics:")
    print(json.dumps(result.protocol, indent=2, sort_keys=True))

    if not result.md5_ok:
        raise SystemExit("transfer integrity check FAILED")


if __name__ == "__main__":
    main()
