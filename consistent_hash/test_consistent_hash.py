"""HashRing 单元测试与边界用例（标准库 unittest）。"""
import unittest

from consistent_hash import HashRing


def make_keys(n, prefix="key"):
    return [f"{prefix}-{i}" for i in range(n)]


class TestBasicMapping(unittest.TestCase):
    def test_single_node_gets_all_keys(self):
        ring = HashRing(vnodes=50)
        ring.add_node("a")
        for k in make_keys(1000):
            self.assertEqual(ring.get_node(k), "a")

    def test_all_nodes_down(self):
        ring = HashRing()
        ring.add_node("a")
        ring.add_node("b")
        ring.remove_node("a")
        ring.remove_node("b")
        self.assertEqual(ring.ring_size, 0)
        with self.assertRaises(RuntimeError):
            ring.get_node("anything")

    def test_empty_ring_raises(self):
        with self.assertRaises(RuntimeError):
            HashRing().get_node("k")

    def test_zero_weight_node_owns_nothing(self):
        ring = HashRing(vnodes=100)
        ring.add_node("a", weight=1)
        ring.add_node("b", weight=0)   # 权重为零：不占环、不接收键
        owners = {ring.get_node(k) for k in make_keys(5000)}
        self.assertEqual(owners, {"a"})
        # 但环上其他节点增删后，零权重节点仍不接收键
        ring.add_node("c", weight=1)
        owners = {ring.get_node(k) for k in make_keys(5000)}
        self.assertNotIn("b", owners)

    def test_order_independence(self):
        """注册顺序不同，键归属必须一致。"""
        r1 = HashRing(vnodes=80)
        for n in ["n1", "n2", "n3", "n4"]:
            r1.add_node(n, weight=2 if n == "n2" else 1)
        r2 = HashRing(vnodes=80)
        for n in ["n4", "n3", "n2", "n1"]:
            r2.add_node(n, weight=2 if n == "n2" else 1)
        keys = make_keys(20000)
        self.assertEqual(r1.map_keys(keys), r2.map_keys(keys))

    def test_weight_proportionality(self):
        ring = HashRing(vnodes=400)
        ring.add_node("heavy", weight=3)
        ring.add_node("light", weight=1)
        keys = make_keys(100000)
        counts = {}
        for k in keys:
            counts[ring.get_node(k)] = counts.get(ring.get_node(k), 0) + 1
        ratio = counts["heavy"] / counts["light"]
        self.assertTrue(2.4 < ratio < 3.6, f"weight ratio off: {ratio}")

    def test_keys_far_outnumber_ring(self):
        """键数(200k) 远大于环规模(3*4=12 个虚拟节点) 时仍可正确分配。"""
        ring = HashRing(vnodes=4)
        for n in ("a", "b", "c"):
            ring.add_node(n)
        self.assertEqual(ring.ring_size, 12)
        keys = make_keys(200000)
        mapping = ring.map_keys(keys)
        self.assertEqual(set(mapping.values()), {"a", "b", "c"})
        # 每个键都能映射，且结果确定
        for k in keys[:100]:
            self.assertEqual(mapping[k], ring.get_node(k))

    def test_duplicate_add_and_unknown_remove(self):
        ring = HashRing()
        ring.add_node("a")
        with self.assertRaises(ValueError):
            ring.add_node("a")
        with self.assertRaises(KeyError):
            ring.remove_node("ghost")
        with self.assertRaises(ValueError):
            ring.add_node("neg", weight=-1)


class TestMigration(unittest.TestCase):
    def test_add_node_only_moves_keys_to_new_node(self):
        keys = make_keys(50000)
        ring = HashRing(vnodes=160)
        for n in ("a", "b", "c"):
            ring.add_node(n)
        before = ring.map_keys(keys)
        ring.add_node("d")
        after = ring.map_keys(keys)
        moved = [k for k in keys if before[k] != after[k]]
        # 不允许迁移与增删无关的键：所有移动的键必须迁往新节点 d
        self.assertTrue(all(after[k] == "d" for k in moved))
        ratio = len(moved) / len(keys)
        self.assertLess(ratio, 0.30)  # 理论下限 1/4，实测应接近

    def test_remove_node_only_moves_its_own_keys(self):
        keys = make_keys(50000)
        ring = HashRing(vnodes=160)
        for n in ("a", "b", "c", "d"):
            ring.add_node(n)
        before = ring.map_keys(keys)
        ring.remove_node("d")
        after = ring.map_keys(keys)
        moved = [k for k in keys if before[k] != after[k]]
        # 只有原本属于 d 的键允许移动
        self.assertTrue(all(before[k] == "d" for k in moved))
        self.assertTrue(all(after[k] != "d" for k in moved))
        ratio = len(moved) / len(keys)
        self.assertLess(ratio, 0.30)  # 理论下限 1/4

    def test_remove_last_but_one(self):
        ring = HashRing()
        ring.add_node("a")
        ring.add_node("b")
        ring.remove_node("b")
        for k in make_keys(500):
            self.assertEqual(ring.get_node(k), "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
