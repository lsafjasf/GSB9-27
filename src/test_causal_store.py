"""Self-tests + scenarios + metadata growth report for the causal version store.

Run:
    python3 src/test_causal_store.py            # prints the scenario report
    python3 -m unittest src.test_causal_store   # machine-checked assertions
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from causal_store import (  # noqa: E402
    CausalViolation,
    StaleReplicaError,
    VersionVector,
    relation,
)
from cluster import Cluster, CausalOracle  # noqa: E402


class VersionVectorRulesTest(unittest.TestCase):
    def test_relations(self):
        a = VersionVector({"n1": 1})
        b = VersionVector({"n1": 2})
        c = VersionVector({"n1": 2, "n2": 1})
        d = VersionVector({"n1": 1, "n2": 2})
        self.assertEqual(relation(a, b), "before")
        self.assertEqual(relation(b, a), "after")
        self.assertEqual(relation(a, a), "equal")
        self.assertEqual(relation(b, c), "before")
        self.assertEqual(relation(c, b), "after")
        self.assertEqual(relation(c, d), "concurrent")
        self.assertEqual(relation(d, c), "concurrent")


class SingleNodeTest(unittest.TestCase):
    def test_linear_overwrites_no_conflict(self):
        cluster = Cluster(["n1"])
        cluster.write("n1", "k", "v1")
        cluster.write("n1", "k", "v2")
        cluster.write("n1", "k", "v3")
        result = cluster.read("n1", "k")
        self.assertFalse(result.conflict)
        self.assertEqual(result.values(), ["v3"])


class ConcurrentWriteTest(unittest.TestCase):
    def test_concurrent_heads_are_both_returned_and_flagged(self):
        cluster = Cluster(["n1", "n2"])
        cluster.write("n1", "k", "base")
        cluster.send_all("n1", "n2")

        a = cluster.write("n1", "k", "from-n1")
        b = cluster.write("n2", "k", "from-n2")
        self.assertEqual(relation(a.vector, b.vector), "concurrent")

        cluster.sync_pair("n1", "n2")
        result = cluster.read("n1", "k")
        self.assertTrue(result.conflict)
        self.assertEqual(sorted(result.values()), ["from-n1", "from-n2"])

        merged = cluster.merge("n1", "k", "merged-value")
        self.assertTrue(all(h.vid != merged.vid for h in result.versions))
        cluster.sync_pair("n1", "n2")
        resolved = cluster.read("n2", "k")
        self.assertFalse(resolved.conflict)
        self.assertEqual(resolved.values(), ["merged-value"])

        print("\n=== conflict sample (two concurrent heads) ===")
        print(json.dumps(result.sample(), indent=2, ensure_ascii=False))


class TransitiveThreeNodeTest(unittest.TestCase):
    def test_cause_travels_n1_to_n2_to_n3_in_shuffled_order(self):
        cluster = Cluster(["n1", "n2", "n3"])
        a = cluster.write("n1", "k", "A")
        cluster.send_all("n1", "n2")
        b = cluster.write("n2", "k", "B")
        self.assertEqual(relation(a.vector, b.vector), "before")

        # n3 meets the wire out of order: B's envelope arrives before A's.
        statuses = cluster.send(
            "n2", "n3",
            [v for v in cluster.nodes["n2"].all_versions() if v.key == "k"][::-1],
        )
        self.assertEqual(statuses, ["buffered", "delivered"])
        node3 = cluster.nodes["n3"]
        self.assertEqual(node3.buffered_count(), 0)
        result = cluster.read("n3", "k")
        self.assertFalse(result.conflict)
        self.assertEqual(result.values(), ["B"])
        delivered = [v.value for v in node3.delivery_log]
        self.assertEqual(delivered, ["A", "B"], "cause A must be applied before effect B")

    def test_shuffled_full_sync_never_violates_causality(self):
        # Each round all three nodes write before syncing: those writes are
        # genuinely concurrent, so heads must converge to the *same conflict
        # set* on every node — never a silently dropped divergent value.
        cluster = Cluster(["n1", "n2", "n3"])
        for round_no in range(7):
            for node in ("n1", "n2", "n3"):
                cluster.write(node, "k", f"{node}-r{round_no}")
            cluster.fully_sync(shuffle=True)
        for node in cluster.nodes.values():
            self.assertEqual(node.buffered_count(), 0)
            cluster.oracle.check_delivery_order(node.delivery_log)
        head_sets = {
            nid: sorted(cluster.read(nid, "k").values())
            for nid in cluster.nodes
        }
        self.assertEqual(len({tuple(v) for v in head_sets.values()}), 1)
        self.assertEqual(len(head_sets["n1"]), 3)
        cluster.merge("n1", "k", "follower-resolve")
        cluster.fully_sync(shuffle=True)
        for nid in cluster.nodes:
            resolved = cluster.read(nid, "k")
            self.assertFalse(resolved.conflict)
            self.assertEqual(resolved.values(), ["follower-resolve"])


class OfflineReturnTest(unittest.TestCase):
    def test_long_offline_node_catches_up_with_buffering_and_conflict(self):
        cluster = Cluster(["n1", "n2", "n3"])
        cluster.write("n1", "k", "base")
        cluster.fully_sync()

        # n3 goes offline. Quorum keeps evolving; n3 evolves on its own.
        online = [f"v{i}" for i in range(1, 9)]
        for i, value in enumerate(online):
            cluster.write(f"n{1 + i % 2}", "k", value)
            cluster.sync_pair("n1", "n2")  # online branch stays linear
        offline_writes = [cluster.write("n3", "k", f"offline-{i}") for i in range(5)]

        # Reconnect: everything arrives at n3 in random order; causal gaps
        # must be buffered, never applied.
        backlog = cluster.nodes["n1"].all_versions()
        statuses = cluster.send("n1", "n3", backlog, shuffle=True)
        self.assertIn("buffered", statuses)
        n3 = cluster.nodes["n3"]
        self.assertEqual(n3.buffered_count(), 0)

        # n3's own branch and the online branch diverged from 'base'.
        result = cluster.read("n3", "k")
        self.assertTrue(result.conflict)
        heads = sorted(result.values())
        self.assertIn("offline-4", heads)
        self.assertIn("v8", heads)

        # Also push n3's branch out; quorum sees the same conflict.
        cluster.sync_pair("n3", "n1", shuffle=True)
        cluster.sync_pair("n1", "n2", shuffle=True)
        for nid in ("n1", "n2", "n3"):
            self.assertTrue(cluster.read(nid, "k").conflict)

        merged = cluster.merge("n2", "k", "reconciled")
        cluster.fully_sync(shuffle=True)
        for nid in cluster.nodes:
            resolved = cluster.read(nid, "k")
            self.assertFalse(resolved.conflict)
            self.assertEqual(resolved.values(), ["reconciled"])
            self.assertTrue(merged.vector.dominates_or_equal(offline_writes[-1].vector))
        print("\n=== offline catch-up: conflict sample ===")
        print(json.dumps(result.sample(), indent=2, ensure_ascii=False))


class CausalReadTest(unittest.TestCase):
    def test_stale_replica_refuses_and_fresh_replica_serves(self):
        cluster = Cluster(["n1", "n2"])
        cluster.write("n1", "k", "v1")
        cluster.send_all("n1", "n2")
        seen_at_n2 = cluster.read("n2", "k").snapshot

        cluster.write("n1", "k", "v2")  # n3-less update n2 has not seen
        self.assertEqual(cluster.read("n2", "k").values(), ["v1"])

        with self.assertRaises(StaleReplicaError):
            # Client comes from a node that had already observed v2 and asks
            # stale n2; serving v1 would roll the client backwards.
            cluster.read("n2", "k", client_snapshot=cluster.read("n1", "k").snapshot)

        cluster.send_all("n1", "n2")
        fresh = cluster.read("n2", "k", client_snapshot=seen_at_n2)
        self.assertEqual(fresh.values(), ["v2"])


class ViolationDetectionTest(unittest.TestCase):
    def test_oracle_flags_effect_without_cause(self):
        cluster = Cluster(["n1", "n2"])
        a = cluster.write("n1", "k", "A")
        cluster.send_all("n1", "n2")
        b = cluster.write("n2", "k", "B")
        # Simulate a buggy/broken transport that applies B but drops A.
        forged_log = [b]
        with self.assertRaises(CausalViolation) as ctx:
            cluster.oracle.check_delivery_order(forged_log)
        self.assertIn("without cause", str(ctx.exception))
        print("\n=== causal violation assertion ===")
        print(f"raised: {ctx.exception!s}")

    def test_buffering_is_what_prevents_the_violation(self):
        # Same A-before-B history arriving at n3 in the wrong wire order:
        # B is held in the gap buffer until A arrives, so the effective
        # delivery order is still A then B.
        cluster = Cluster(["n1", "n2", "n3"])
        cluster.write("n1", "k", "A")
        cluster.send_all("n1", "n2")
        cluster.write("n2", "k", "B")
        a = next(v for v in cluster.nodes["n2"].all_versions() if v.value == "A")
        b = next(v for v in cluster.nodes["n2"].all_versions() if v.value == "B")
        self.assertEqual(cluster.nodes["n3"].receive(b), "buffered")
        # While B waits for A, nothing of the effect is observable.
        self.assertEqual(cluster.read("n3", "k").versions, ())
        self.assertEqual(cluster.nodes["n3"].receive(a), "delivered")
        self.assertEqual(cluster.nodes["n3"].buffered_count(), 0)
        self.assertEqual(
            [v.value for v in cluster.nodes["n3"].delivery_log], ["A", "B"]
        )


# ---------------------------------------------------------------------------
# Metadata growth report
# ---------------------------------------------------------------------------


def measure_metadata() -> dict:
    report = {}

    cluster = Cluster(["n1"])
    for i in range(1, 101):
        cluster.write("n1", "k", f"v{i}")
    vv = cluster.nodes["n1"].clock
    last = cluster.nodes["n1"].all_versions()[-1]
    report["single_node_100_writes"] = {
        "vector_coordinates": len(vv.to_dict()),
        "vector_bytes": vv.metadata_bytes(),
        "per_version_envelope_bytes": last.envelope_bytes(),
    }

    def growing_quorum(nodes: int, writes_per_node: int):
        c = Cluster([f"n{i}" for i in range(1, nodes + 1)])
        for r in range(writes_per_node):
            for nid in c.nodes:
                c.write(nid, "k", f"{nid}-r{r}")
            c.fully_sync()
        clock = next(iter(c.nodes.values())).clock
        envelope = next(iter(c.nodes.values())).all_versions()[-1].envelope_bytes()
        return {
            "nodes": nodes,
            "total_writes": nodes * writes_per_node,
            "vector_coordinates": len(clock.to_dict()),
            "vector_bytes": clock.metadata_bytes(),
            "per_version_envelope_bytes": envelope,
        }

    report["quorum_scaling"] = [growing_quorum(n, 10) for n in (2, 3, 5, 10)]

    cluster = Cluster(["n1", "n2", "n3"])
    cluster.write("n1", "k", "base")
    cluster.fully_sync()
    for i in range(8):
        cluster.write(f"n{1 + i % 2}", "k", f"v{i}")
    cluster.sync_pair("n1", "n2")
    for i in range(5):
        cluster.write("n3", "k", f"offline-{i}")
    cluster.send("n1", "n3", cluster.nodes["n1"].all_versions(), shuffle=True)
    n3 = cluster.nodes["n3"]
    report["offline_return_3_nodes"] = {
        "total_versions_at_returning_node": len(n3.all_versions()),
        "vector_bytes": n3.clock.metadata_bytes(),
        "stored_metadata_bytes_on_node": n3.stored_metadata_bytes(),
        "buffered_after_drain": n3.buffered_count(),
    }
    return report


def _run_report() -> None:
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for case in (
        VersionVectorRulesTest,
        SingleNodeTest,
        ConcurrentWriteTest,
        TransitiveThreeNodeTest,
        OfflineReturnTest,
        CausalReadTest,
        ViolationDetectionTest,
    ):
        suite.addTests(loader.loadTestsFromTestCase(case))
    unittest.TextTestRunner(verbosity=2).run(suite)
    print("\n=== metadata growth (bytes on the wire, JSON-encoded) ===")
    print(json.dumps(measure_metadata(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _run_report()
