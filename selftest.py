"""自测：递归折叠、树聚合正确性、四类边界场景。

运行: python3 selftest.py [-v]
"""

from __future__ import annotations

import unittest

import scenarios
from sampler import FrameKey, StackTree, fold_frames

A = FrameKey("a", "f.py", 1)
B = FrameKey("b", "f.py", 5)
C = FrameKey("c", "f.py", 9)


class TestFold(unittest.TestCase):
    def test_direct_recursion_folds(self):
        self.assertEqual(fold_frames([A, A, A, B]), [A, B])
        self.assertEqual(fold_frames([A, A, A]), [A])
        self.assertEqual(fold_frames([]), [])

    def test_indirect_recursion_keeps_path(self):
        # 非连续同名（间接递归 a->b->a）不折叠：路径信息保留
        self.assertEqual(fold_frames([A, B, A, B]), [A, B, A, B])

    def test_deep_recursion_folds_constant_size(self):
        keys = [A] * 100_000 + [B]
        result = fold_frames(keys)
        self.assertEqual(result, [A, B])


class TestTree(unittest.TestCase):
    def test_self_and_total_counts(self):
        tree = StackTree()
        tree.add([A, B])       # 栈: a -> b
        tree.add([A, B])
        tree.add([A, C])
        tree.add([A])
        # a: total=4, self=1 ; b: total=2, self=2 ; c: total=1, self=1
        stats = tree.func_stats()
        self.assertEqual(stats["a"], (1, 4))
        self.assertEqual(stats["b"], (2, 2))
        self.assertEqual(stats["c"], (1, 1))

    def test_recursive_stack_one_node(self):
        tree = StackTree()
        tree.add(fold_frames([A, A, A]))
        tree.add(fold_frames([A, A, B]))
        a_nodes = [n for n in tree.iter_nodes() if n.key.func == "a"]
        self.assertEqual(len(a_nodes), 1)
        stats = tree.func_stats()
        # 第 1 条自身在 a，第 2 条自身在 b；a 两次都在路径上
        self.assertEqual(stats["a"], (1, 2))
        self.assertEqual(stats["b"], (1, 1))

    def test_percentages_and_lines(self):
        tree = StackTree()
        tree.add([A, B])
        text = "\n".join(tree.lines())
        self.assertIn("b", text)
        self.assertIn("100.00", text)  # b 累计 100%

    def test_iter_nodes_survives_deep_tree(self):
        tree = StackTree()
        keys = [FrameKey(f"f{i}", "x.py", i) for i in range(10_000)]
        tree.add(keys)
        tree.lines()  # 渲染也不能触发递归深度问题
        self.assertEqual(tree.samples, 1)


class TestScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.short = scenarios.scenario_short_calls()
        cls.single = scenarios.scenario_single_level()
        cls.deep = scenarios.scenario_deep_stack()
        cls.fast = scenarios.scenario_fast_sampling()

    def test_short_calls_runs_with_data(self):
        self.assertGreater(self.short["samples"], 50)
        self.assertGreater(self.short["tiny_calls"], self.short["samples"])
        self.assertGreaterEqual(self.short["tiny_hit_pct"], 0.0)

    def test_single_level_shape(self):
        # _driver_one 之下主路径只能是 _leaf_one -> _spin_ms（单层调用）
        self.assertEqual(self.single["chain_below_driver"],
                         ["_leaf_one", "_spin_ms"])
        self.assertIn("_leaf_one", self.single["nodes"])

    def test_deep_stack_folds_to_single_node(self):
        self.assertEqual(self.deep["synthetic_folded_len"], 2)
        self.assertEqual(self.deep["rec_node_count"], 1)
        # 900 层递归折叠为 1 个节点，其父节点直接是场景函数
        self.assertEqual(self.deep["rec_parent_func"], "scenario_deep_stack")
        self.assertGreater(self.deep["rec_total_hits"], 0)

    def test_fast_sampling_no_duplicate_nodes(self):
        self.assertEqual(self.fast["duplicate_parent_child_edges"], 0)
        self.assertEqual(self.fast["heavy_node_count"], 1)
        self.assertGreater(self.fast["samples_per_call"], 1.0)
        self.assertGreater(self.fast["heavy_total_pct"], 80.0)

    def test_loss_report_well_formed(self):
        for result in (self.short, self.single, self.deep, self.fast):
            loss = result["loss"]
            self.assertGreaterEqual(loss["expected"], 0)
            self.assertGreaterEqual(loss["lost_estimate"], 0)
            self.assertEqual(
                loss["taken"], result["samples"]
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
