"""Reproducibility tests for the network injector.

Run: python3 -m unittest tests.test_reproducibility -v
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netinject import (  # noqa: E402
    DIRECTIONS,
    DOWNLINK,
    UPLINK,
    NetworkConfig,
    NetworkInjector,
    Profile,
    Segment,
    SegmentedProfile,
    Simulator,
    percentile,
)


def run_trace(seed, count=200):
    """Send ``count`` messages and return every delivery + final stats."""
    config = NetworkConfig(
        downlink=SegmentedProfile([
            Segment(0.0, Profile(base_delay=0.02, jitter=0.005)),
            Segment(3.0, Profile(base_delay=0.02, jitter=0.005, loss=0.15,
                                 duplicate=0.05, reorder=0.10,
                                 reorder_extra_delay=0.10)),
        ]),
        uplink=SegmentedProfile([
            Segment(0.0, Profile(base_delay=0.01, jitter=0.002)),
            Segment(3.0, Profile(base_delay=0.01, jitter=0.002, loss=0.05,
                                 bandwidth_bps=128_000)),
        ]),
    )
    delivered = {"downlink": [], "uplink": []}
    net = NetworkInjector(
        config,
        seed=seed,
        on_downlink=lambda t, s, m: delivered[DOWNLINK].append((round(t, 9), s, m)),
        on_uplink=lambda t, s, m: delivered[UPLINK].append((round(t, 9), s, m)),
    )
    for i in range(count):
        net.sim.schedule(0.02 * i, lambda _t, k=i: net.send_downlink(b"d%04d" % k, 120))
        net.sim.schedule(0.02 * i + 0.01, lambda _t, k=i: net.send_uplink(b"u%04d" % k, 50))
    net.run()
    return delivered, net.stats_summary(), net.time


class ReproducibilityTests(unittest.TestCase):
    def test_same_seed_identical_trace(self):
        for seed in (0, 1, 7, 20260928):
            trace_a, stats_a, time_a = run_trace(seed)
            trace_b, stats_b, time_b = run_trace(seed)
            self.assertEqual(trace_a, trace_b)
            self.assertEqual(stats_a, stats_b)
            self.assertEqual(time_a, time_b)

    def test_deterministic_without_dict_order_dependence(self):
        # Running the trace in a fresh process-level RNG state must not matter:
        # the injector derives everything from its own seed.
        random.seed("wall clock noise")
        trace_a, _, _ = run_trace(99)
        random.seed(123456789)
        trace_b, _, _ = run_trace(99)
        self.assertEqual(trace_a, trace_b)

    def test_different_seeds_usually_differ(self):
        traces = set()
        for seed in range(20):
            trace, stats, _ = run_trace(seed, count=100)
            traces.add(repr(trace))
        # With 15% loss over 100 messages, collisions are vanishingly unlikely.
        self.assertGreater(len(traces), 15)

    def test_stats_match_trace(self):
        trace, stats, _ = run_trace(555, count=300)
        for direction in DIRECTIONS:
            deliveries = trace[direction]
            first_seqs = set()
            reorders = 0
            last_seq = -1
            for _time, seq, _msg in deliveries:
                if seq not in first_seqs:
                    first_seqs.add(seq)
                    if seq < last_seq:
                        reorders += 1
                    last_seq = max(last_seq, seq)
            self.assertEqual(stats[direction]["delivered"], len(first_seqs))
            self.assertEqual(stats[direction]["reorders"], reorders)
            self.assertEqual(
                stats[direction]["delivered"] + stats[direction]["lost"],
                stats[direction]["sent"],
            )

    def test_bandwidth_sets_lower_bound_on_delivery_times(self):
        profile = SegmentedProfile.constant(
            Profile(base_delay=0.0, bandwidth_bps=8000)  # 1000 bytes/s
        )
        delivered = []
        net = NetworkInjector(
            NetworkConfig.symmetric(profile), seed=3,
            on_downlink=lambda t, s, m: delivered.append((t, s)),
        )
        # Three 1000-byte messages sent at once must take >=0s, >=1s, >=2s wire time.
        for _ in range(3):
            net.send_downlink(b"x" * 1000, 1000)
        net.run()
        times = sorted(t for t, _ in delivered)
        self.assertAlmostEqual(times[0], 1.0, delta=1e-9)
        self.assertAlmostEqual(times[1], 2.0, delta=1e-9)
        self.assertAlmostEqual(times[2], 3.0, delta=1e-9)

    def test_percentile_nearest_rank(self):
        values = [float(x) for x in range(1, 10)]
        self.assertEqual(percentile(values, 50), 5.0)
        self.assertEqual(percentile(values, 99), 9.0)
        self.assertEqual(percentile([0.5], 99), 0.5)

    def test_segment_boundary_profile(self):
        profile = SegmentedProfile([
            Segment(0.0, Profile(loss=0.0)),
            Segment(1.0, Profile(loss=0.5)),
            Segment(2.5, Profile(loss=1.0)),
        ])
        self.assertEqual(profile.at(0.0).loss, 0.0)
        self.assertEqual(profile.at(0.999).loss, 0.0)
        self.assertEqual(profile.at(1.0).loss, 0.5)
        self.assertEqual(profile.at(2.49).loss, 0.5)
        self.assertEqual(profile.at(2.5).loss, 1.0)
        self.assertEqual(profile.at(99.0).loss, 1.0)

    def test_invalid_parameters_rejected(self):
        with self.assertRaises(ValueError):
            Profile(loss=1.1)
        with self.assertRaises(ValueError):
            Profile(base_delay=-0.1)
        with self.assertRaises(ValueError):
            SegmentedProfile([Segment(1.0, Profile())])

    def test_zero_loss_zero_jitter_delivers_everything_in_order(self):
        profile = SegmentedProfile.constant(Profile(base_delay=0.03))
        delivered = []
        net = NetworkInjector(
            NetworkConfig.symmetric(profile), seed=11,
            on_downlink=lambda t, s, m: delivered.append(s),
        )
        for i in range(50):
            net.sim.schedule(i * 0.01, lambda _t, k=i: net.send_downlink(k, size_bytes=10))
        net.run()
        self.assertEqual(delivered, list(range(50)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
