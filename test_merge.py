"""复现用例 + 修复后回归测试。运行：python3 -m unittest -v 或 python3 test_merge.py"""

import itertools
import random
import unittest

from legacy_merge import LegacyStore
from sync_merge import Meta, Store


def make_update_store(node, ops):
    """把一组操作做在一个独立 Store 上，作为一个「更新单元」。"""
    s = Store(node)
    for op in ops:
        op(s)
    return s


class ReproduceBugTests(unittest.TestCase):
    """稳定复现：两侧时间戳完全相同、来源标识缺失、到达顺序不同。"""

    def _conflicting_ops(self):
        # 同一字段、相同时间戳、来源标识缺失（source=""）的两个并发写
        return [
            lambda s: s.set_field("doc1", "title", "from-X", timestamp=100, source=""),
            lambda s: s.set_field("doc1", "title", "from-Y", timestamp=100, source=""),
        ]

    def test_legacy_merge_is_order_dependent(self):
        ops = self._conflicting_ops()
        end1, end2 = LegacyStore("L1"), LegacyStore("L2")
        for op in ops:  # 端1：X 先到
            op(end1)
        for op in reversed(ops):  # 端2：Y 先到
            op(end2)
        # 复现不一致：同一批更新，两端合并结果不同（这就是用户看到的「跳来跳去」）
        self.assertNotEqual(end1.get("doc1"), end2.get("doc1"))

    def test_fixed_merge_is_order_independent(self):
        ops = self._conflicting_ops()
        updates = [make_update_store(f"u{i}", [op]) for i, op in enumerate(ops)]
        end1, end2 = Store("L1"), Store("L2")
        for u in updates:
            end1.merge(u)
        for u in reversed(updates):
            end2.merge(u)
        self.assertEqual(end1.get("doc1"), end2.get("doc1"))
        self.assertEqual(end1.snapshot(), end2.snapshot())


class TieBreakRuleTests(unittest.TestCase):
    """决胜规则：version 优先，再比 timestamp，最后比 source（缺失视为 ""）。"""

    def test_version_beats_timestamp(self):
        # 时钟回拨场景：version=2 的写时间戳反而更小，仍必须获胜
        self.assertGreater(Meta(2, 50, "A"), Meta(1, 9999, "Z"))

    def test_timestamp_breaks_equal_version(self):
        self.assertGreater(Meta(1, 200, "A"), Meta(1, 100, "B"))

    def test_source_breaks_full_tie(self):
        self.assertGreater(Meta(1, 100, "B"), Meta(1, 100, "A"))

    def test_missing_source_sorts_lowest(self):
        self.assertLess(Meta(1, 100, ""), Meta(1, 100, "A"))
        self.assertEqual(Meta(1, 100, ""), Meta(1, 100, ""))

    def test_missing_source_still_deterministic(self):
        # 来源缺失 + 时间戳相同：结果由 version 决定，与到达顺序无关
        u1 = make_update_store("u1", [lambda s: s.set_field("k", "f", "v1", 100, "")])
        u2 = make_update_store("u2", [lambda s: s.set_field("k", "f", "v2", 100, "")])
        a, b = Store("A"), Store("B")
        a.merge(u1); a.merge(u2)
        b.merge(u2); b.merge(u1)
        self.assertEqual(a.get("k"), b.get("k"))
        self.assertEqual(a.get("k"), {"f": "v2"})  # u2 的 version=2 更大


class DeleteVsUpdateTests(unittest.TestCase):
    """删除（墓碑）与更新竞争，两种到达顺序必须收敛到同一结果。"""

    def _run_both_orders(self, ops_a, ops_b):
        ua = make_update_store("A", ops_a)
        ub = make_update_store("B", ops_b)
        end1, end2 = Store("L1"), Store("L2")
        end1.merge(ua); end1.merge(ub)
        end2.merge(ub); end2.merge(ua)
        self.assertEqual(end1.snapshot(), end2.snapshot())
        return end1

    def test_delete_wins_when_newer(self):
        # A 先写字段（v1）；B 合并后删除（v2，因果上更新，且时钟回拨 ts=50）
        ua = make_update_store("A", [lambda s: s.set_field("k", "f", "v", 100)])
        ub = Store("B")
        ub.merge(ua)
        ub.delete("k", timestamp=50)  # version=2 > 1，时钟回拨也无关
        end1, end2 = Store("L1"), Store("L2")
        end1.merge(ua); end1.merge(ub)
        end2.merge(ub); end2.merge(ua)
        self.assertEqual(end1.snapshot(), end2.snapshot())
        end = end1
        self.assertIsNone(end.get("k"))

    def test_update_wins_when_newer(self):
        # A 先删除（v1）；B 合并后写字段（v2，因果上更新）-> 字段复活
        ua = make_update_store("A", [lambda s: s.delete("k", timestamp=100)])
        ub = Store("B")
        ub.merge(ua)
        ub.set_field("k", "f", "v", 50)
        end1, end2 = Store("L1"), Store("L2")
        end1.merge(ua); end1.merge(ub)
        end2.merge(ub); end2.merge(ua)
        self.assertEqual(end1.snapshot(), end2.snapshot())
        end = end1
        self.assertEqual(end.get("k"), {"f": "v"})

    def test_delete_is_idempotent_under_duplicate_delivery(self):
        u = make_update_store("A", [lambda s: s.delete("k")])
        end = Store("L")
        end.merge(u); end.merge(u); end.merge(u)
        self.assertIsNone(end.get("k"))


class ClockRollbackTests(unittest.TestCase):
    def test_rollback_does_not_flip_outcome(self):
        # 节点 A 时钟回拨：第二次写的时间戳(50) < 第一次(200)，但 version 递增
        a = Store("A")
        a.set_field("k", "f", "old", timestamp=200)
        a.set_field("k", "f", "new", timestamp=50)
        self.assertEqual(a.get("k"), {"f": "new"})

    def test_rollback_across_nodes_converges(self):
        # A 回拨后的写（v2, ts=50）vs B 的写（v1, ts=150）：version 决定胜负
        ua = Store("A")
        ua.set_field("k", "f", "a1", timestamp=300)
        ua.set_field("k", "f", "a2", timestamp=50)
        ub = make_update_store("B", [lambda s: s.set_field("k", "f", "b1", 150)])
        end1, end2 = Store("L1"), Store("L2")
        end1.merge(ua); end1.merge(ub)
        end2.merge(ub); end2.merge(ua)
        self.assertEqual(end1.get("k"), end2.get("k"))
        self.assertEqual(end1.get("k"), {"f": "a2"})


class MixedLevelMergeTests(unittest.TestCase):
    """字段级与记录级混合合并。"""

    def test_field_write_overrides_older_record_write(self):
        u1 = make_update_store("A", [lambda s: s.set_record("k", {"x": 1, "y": 1})])
        u2 = make_update_store("B", [lambda s: s.set_field("k", "x", 9)])
        end1, end2 = Store("L1"), Store("L2")
        end1.merge(u1); end1.merge(u2)
        end2.merge(u2); end2.merge(u1)
        self.assertEqual(end1.get("k"), end2.get("k"))
        self.assertEqual(end1.get("k"), {"x": 9, "y": 1})  # x 字段级更新，y 保留记录级

    def test_newer_record_write_overrides_field_write(self):
        u1 = make_update_store("A", [lambda s: s.set_field("k", "x", 9)])
        u2 = make_update_store("B", [lambda s: s.set_record("k", {"x": 5, "z": 5})])
        # u2 的 version(1) 与 u1 的 version(1) 相同、时间戳相同 -> source "B" > "A"
        end1, end2 = Store("L1"), Store("L2")
        end1.merge(u1); end1.merge(u2)
        end2.merge(u2); end2.merge(u1)
        self.assertEqual(end1.get("k"), end2.get("k"))
        self.assertEqual(end1.get("k"), {"x": 5, "z": 5})

    def test_record_delete_then_field_resurrect_mixed(self):
        u1 = make_update_store("A", [lambda s: s.set_record("k", {"x": 1})])
        u2 = make_update_store("B", [lambda s: s.delete("k")])
        u3 = make_update_store("C", [lambda s: s.set_field("k", "y", 7)])
        snapshots = set()
        for perm in itertools.permutations([u1, u2, u3]):
            end = Store("L")
            for u in perm:
                end.merge(u)
            snapshots.add(repr(sorted(end.snapshot()["data"].items())))
        self.assertEqual(len(snapshots), 1)  # 全部 6 种顺序收敛


class ConvergenceTests(unittest.TestCase):
    """收敛性：任意端以任意顺序合并同一组更新，最终值与版本向量一致。"""

    def _assert_converges(self, updates):
        snapshots = set()
        for perm in itertools.permutations(updates):
            end = Store("replica")
            for u in perm:
                end.merge(u)
            snap = end.snapshot()
            snapshots.add(repr(snap["data"]))
            # 版本向量也必须一致：逐分量 max
            expected_vv = {}
            for u in updates:
                for node, v in u.version_vector.items():
                    expected_vv[node] = max(expected_vv.get(node, 0), v)
            self.assertEqual(snap["version_vector"], expected_vv)
        self.assertEqual(len(snapshots), 1, "不同合并顺序产生了不同结果")

    def test_all_permutations_of_five_updates(self):
        updates = [
            make_update_store("A", [lambda s: s.set_field("k1", "f", "a", 100, "")]),
            make_update_store("B", [lambda s: s.set_field("k1", "f", "b", 100, "")]),
            make_update_store("C", [lambda s: s.set_record("k1", {"f": "c", "g": 1}, 100)]),
            make_update_store("D", [lambda s: s.delete("k1", 100)]),
            make_update_store("E", [lambda s: s.set_field("k1", "g", 2, 100)]),
        ]
        self._assert_converges(updates)  # 120 种顺序

    def test_random_gossip_converges(self):
        rng = random.Random(20260928)
        nodes = ["A", "B", "C"]
        updates = []
        for i in range(30):
            node = rng.choice(nodes)
            key = f"k{rng.randrange(4)}"
            ts = rng.choice([0, 50, 100])  # 大量相同时间戳
            src = rng.choice([node, ""])   # 部分更新来源缺失
            kind = rng.randrange(3)
            if kind == 0:
                op = lambda s, k=key, t=ts, sr=src: s.set_field(k, "f", f"{node}{i}", t, sr)
            elif kind == 1:
                op = lambda s, k=key, t=ts, sr=src: s.set_record(k, {"f": f"{node}{i}"}, t, sr)
            else:
                op = lambda s, k=key, t=ts, sr=src: s.delete(k, t, sr)
            updates.append(make_update_store(f"{node}#{i}", [op]))
        snapshots = set()
        for _ in range(20):  # 20 个副本，各自随机顺序合并
            end = Store("replica")
            order = updates[:]
            rng.shuffle(order)
            for u in order:
                end.merge(u)
            snapshots.add(repr(end.snapshot()))
        self.assertEqual(len(snapshots), 1)

    def test_merge_is_idempotent_and_commutative(self):
        u1 = make_update_store("A", [lambda s: s.set_field("k", "f", "a")])
        u2 = make_update_store("B", [lambda s: s.delete("k")])
        p, q = Store("P"), Store("Q")
        p.merge(u1); p.merge(u2); p.merge(u1)  # 重复投递
        q.merge(u2); q.merge(u1)
        self.assertEqual(p.snapshot(), q.snapshot())


if __name__ == "__main__":
    unittest.main(verbosity=2)
