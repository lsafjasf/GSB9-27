"""Self tests for consistent_hash. Run: python3 -m unittest -v test_consistent_hash"""

import unittest

from consistent_hash import HashRing


def make_ring(node_weights, vnodes=160):
    ring = HashRing(vnodes_per_weight=vnodes)
    for node_id, weight in node_weights:
        ring.add_node(node_id, weight)
    return ring


class TestEdgeCases(unittest.TestCase):
    def test_single_node_gets_everything(self):
        ring = make_ring([("only", 1.0)])
        owners = {ring.get_node("key-%d" % i) for i in range(10000)}
        self.assertEqual(owners, {"only"})

    def test_empty_ring_returns_none(self):
        ring = HashRing()
        self.assertIsNone(ring.get_node("anything"))
        self.assertEqual(ring.get_nodes("anything", 3), [])

    def test_all_nodes_removed(self):
        ring = make_ring([("a", 1.0), ("b", 1.0), ("c", 1.0)])
        for node_id in ("a", "b", "c"):
            ring.remove_node(node_id)
        self.assertEqual(ring.ring_size, 0)
        self.assertIsNone(ring.get_node("key-1"))
        self.assertEqual(ring.ownership(), {})

    def test_zero_weight_node_owns_nothing(self):
        ring = make_ring([("hot", 1.0), ("cold", 0.0)])
        owners = {ring.get_node("key-%d" % i) for i in range(20000)}
        self.assertEqual(owners, {"hot"})
        self.assertEqual(ring.ownership()["cold"], 0)
        # zero-weight node can later be removed without moving any key
        before = {("key-%d" % i): ring.get_node("key-%d" % i) for i in range(5000)}
        ring.remove_node("cold")
        for key, owner in before.items():
            self.assertEqual(ring.get_node(key), owner)

    def test_keys_far_exceed_ring_size(self):
        # 1M keys against a tiny ring (2 nodes x 4 vnodes = 8 points)
        ring = make_ring([("a", 1.0), ("b", 1.0)], vnodes=4)
        self.assertEqual(ring.ring_size, 8)
        counts = {"a": 0, "b": 0}
        for i in range(1_000_000):
            counts[ring.get_node("k%d" % i)] += 1
        self.assertEqual(sum(counts.values()), 1_000_000)
        # every key still lands on a live node
        self.assertTrue(all(c > 0 for c in counts.values()))

    def test_invalid_operations(self):
        ring = HashRing()
        with self.assertRaises(ValueError):
            HashRing(vnodes_per_weight=0)
        ring.add_node("a", 1.0)
        with self.assertRaises(ValueError):
            ring.add_node("a", 1.0)          # duplicate
        with self.assertRaises(ValueError):
            ring.add_node("b", -1.0)         # negative weight
        with self.assertRaises(KeyError):
            ring.remove_node("missing")


class TestOrderIndependence(unittest.TestCase):
    def test_registration_order_does_not_matter(self):
        members = [("n%d" % i, 1.0 + (i % 3)) for i in range(8)]
        ring_a = make_ring(members)
        ring_b = make_ring(list(reversed(members)))
        self.assertEqual(ring_a._ring, ring_b._ring)
        for i in range(20000):
            key = "key-%d" % i
            self.assertEqual(ring_a.get_node(key), ring_b.get_node(key))


class TestMigration(unittest.TestCase):
    def setUp(self):
        self.keys = ["key-%d" % i for i in range(100000)]

    def _mapping(self, ring):
        return {key: ring.get_node(key) for key in self.keys}

    def test_add_node_moves_only_to_new_node(self):
        ring = make_ring([("n%d" % i, 1.0) for i in range(10)])
        before = self._mapping(ring)
        ring.add_node("new", 1.0)
        after = self._mapping(ring)
        moved = 0
        for key in self.keys:
            if before[key] != after[key]:
                moved += 1
                # keys may only move TO the added node, never between old nodes
                self.assertEqual(after[key], "new")
        ratio = moved / len(self.keys)
        # theoretical minimum for 1 of 11 equal nodes: 1/11 ~ 9.09%
        self.assertLess(ratio, 0.15)
        self.assertGreater(ratio, 0.05)

    def test_remove_node_moves_only_from_removed_node(self):
        ring = make_ring([("n%d" % i, 1.0) for i in range(10)])
        before = self._mapping(ring)
        ring.remove_node("n3")
        after = self._mapping(ring)
        moved = 0
        for key in self.keys:
            if before[key] != after[key]:
                moved += 1
                # only keys previously on n3 may move
                self.assertEqual(before[key], "n3")
        ratio = moved / len(self.keys)
        # theoretical minimum: n3's share 1/10 = 10%
        self.assertLess(ratio, 0.16)
        self.assertGreater(ratio, 0.06)

    def test_add_then_remove_restores_mapping(self):
        ring = make_ring([("n%d" % i, 1.0) for i in range(6)])
        before = self._mapping(ring)
        ring.add_node("temp", 2.0)
        ring.remove_node("temp")
        self.assertEqual(before, self._mapping(ring))

    def test_weighted_migration_share(self):
        # adding a node with weight 3 to 9 units of weight: theory 3/12 = 25%
        ring = make_ring([("n%d" % i, 1.0) for i in range(9)])
        before = self._mapping(ring)
        ring.add_node("heavy", 3.0)
        after = self._mapping(ring)
        moved = sum(1 for k in self.keys if before[k] != after[k])
        ratio = moved / len(self.keys)
        self.assertLess(ratio, 0.32)
        self.assertGreater(ratio, 0.18)


class TestWeightsAndDistribution(unittest.TestCase):
    def test_weighted_ownership_proportional(self):
        ring = make_ring([("a", 2.0), ("b", 1.0)], vnodes=500)
        shares = ring.ownership()
        self.assertAlmostEqual(shares["a"] / shares["b"], 2.0, delta=0.3)

    def test_get_nodes_returns_distinct_ordered(self):
        ring = make_ring([("n%d" % i, 1.0) for i in range(5)])
        result = ring.get_nodes("some-key", 3)
        self.assertEqual(len(result), 3)
        self.assertEqual(len(set(result)), 3)
        self.assertEqual(result, ring.get_nodes("some-key", 3))  # deterministic
        self.assertEqual(len(ring.get_nodes("some-key", 99)), 5)  # capped


if __name__ == "__main__":
    unittest.main()
