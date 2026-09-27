"""Tests for the stop-and-wait ARQ example under injection.

Run: python3 -m unittest tests.test_arq_protocol -v
"""

import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netinject import (  # noqa: E402
    NetworkConfig,
    Profile,
    Segment,
    SegmentedProfile,
    run_transfer,
)


def payload(size):
    data = bytearray(size)
    state = 0xA5A5_0001
    for i in range(size):
        state = (state * 2_269_546 + 31) & 0xFFFF_FFFF
        data[i] = (state ^ i) & 0xFF
    return bytes(data)


PAYLOAD = payload(20 * 1024)  # 20 chunks of 1 KiB


def loss_config(loss, duplicate=0.0, reorder=0.0):
    p = Profile(base_delay=0.05, jitter=0.01, loss=loss,
                duplicate=duplicate, reorder=reorder,
                reorder_extra_delay=0.2)
    return NetworkConfig(
        downlink=SegmentedProfile.constant(p),
        uplink=SegmentedProfile.constant(
            Profile(base_delay=0.05, jitter=0.01, loss=loss,
                    duplicate=duplicate, reorder=reorder,
                    reorder_extra_delay=0.2)
        ),
    )


class ArqProtocolTests(unittest.TestCase):
    def test_completes_under_10_percent_loss(self):
        result = run_transfer(PAYLOAD, loss_config(0.10), seed=4242)
        self.assertTrue(result.md5_ok)
        self.assertEqual(len(result.received), len(PAYLOAD))
        self.assertEqual(result.protocol["chunks_total"], 20)
        # Loss happened on the wire, otherwise the test proves nothing.
        total_lost = sum(result.network[d]["lost"] for d in result.network)
        self.assertGreater(total_lost, 0)
        # Retransmission was required and bounded.
        self.assertGreater(result.protocol["data_retransmits"], 0)
        self.assertLessEqual(result.protocol["max_attempts_for_one_chunk"], 10)

    def test_completes_across_many_seeds(self):
        for seed in range(10):
            result = run_transfer(PAYLOAD, loss_config(0.10), seed=seed)
            self.assertTrue(result.md5_ok, "integrity failed for seed %d" % seed)
            self.assertEqual(result.finish_time > 0, True)

    def test_completes_with_duplicates_and_reordering(self):
        result = run_transfer(PAYLOAD, loss_config(0.08, duplicate=0.10, reorder=0.15), seed=77)
        self.assertTrue(result.md5_ok)
        total_dups = sum(result.network[d]["duplicate_deliveries"] for d in result.network)
        total_reorders = sum(result.network[d]["reorders"] for d in result.network)
        self.assertGreater(total_dups, 0)
        self.assertGreater(total_reorders, 0)

    def test_deterministic_protocol_run(self):
        a = run_transfer(PAYLOAD, loss_config(0.10), seed=4242)
        b = run_transfer(PAYLOAD, loss_config(0.10), seed=4242)
        self.assertEqual(a.received, b.received)
        self.assertEqual(a.protocol, b.protocol)
        self.assertEqual(a.network, b.network)
        self.assertEqual(a.finish_time, b.finish_time)

    def test_no_loss_no_retransmit(self):
        clean = NetworkConfig.symmetric(
            SegmentedProfile.constant(Profile(base_delay=0.02, jitter=0.002))
        )
        result = run_transfer(PAYLOAD, clean, seed=1)
        self.assertTrue(result.md5_ok)
        self.assertEqual(result.protocol["data_retransmits"], 0)
        self.assertEqual(result.protocol["timeouts"], 0)
        self.assertEqual(result.protocol["data_sent"], 20)

    def test_time_segmented_loss(self):
        # Loss ramps up mid-transfer; the transfer must still finish.
        downlink = SegmentedProfile([
            Segment(0.0, Profile(base_delay=0.05, jitter=0.01, loss=0.0)),
            Segment(1.0, Profile(base_delay=0.05, jitter=0.01, loss=0.25)),
        ])
        uplink = SegmentedProfile([
            Segment(0.0, Profile(base_delay=0.05, jitter=0.01, loss=0.0)),
            Segment(1.0, Profile(base_delay=0.05, jitter=0.01, loss=0.25)),
        ])
        result = run_transfer(PAYLOAD, NetworkConfig(downlink=downlink, uplink=uplink), seed=9)
        self.assertTrue(result.md5_ok)
        self.assertGreater(result.protocol["data_retransmits"], 0)

    def test_md5_documented_for_sample(self):
        result = run_transfer(PAYLOAD, loss_config(0.10), seed=4242)
        self.assertEqual(
            hashlib.md5(result.received).hexdigest(),
            hashlib.md5(PAYLOAD).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
