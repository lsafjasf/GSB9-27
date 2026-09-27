"""边界用例：单块、空图、只有前向边、循环、不可达块、异常边、嵌套循环。"""

import unittest

from cfggen import gen_nested_loops
from liveness import (BasicBlock, Instr, solve_liveness_naive,
                      solve_liveness_worklist)

SOLVERS = [solve_liveness_worklist, solve_liveness_naive]


class EdgeCases(unittest.TestCase):
    def assert_both(self, blocks, exp_in, exp_out):
        for solve in SOLVERS:
            r = solve(blocks)
            for b in blocks:
                self.assertEqual(r.live_in[b], exp_in.get(b, set()),
                                 "%s live_in[%s]" % (solve.__name__, b))
                self.assertEqual(r.live_out[b], exp_out.get(b, set()),
                                 "%s live_out[%s]" % (solve.__name__, b))

    def test_empty_graph(self):
        for solve in SOLVERS:
            r = solve({})
            self.assertEqual(r.live_in, {})
            self.assertEqual(r.live_out, {})

    def test_single_block_no_instr(self):
        self.assert_both({0: BasicBlock(0)}, {0: set()}, {0: set()})

    def test_single_block_uses(self):
        b = BasicBlock(0, [Instr(uses=frozenset({"x"}))])
        self.assert_both({0: b}, {0: {"x"}}, {0: set()})

    def test_single_block_def_then_use_not_live(self):
        # x = 1; y = x  —— x 先定值后使用，不向上暴露；y 被定值
        b = BasicBlock(0, [Instr(defs=frozenset({"x"})),
                           Instr(defs=frozenset({"y"}), uses=frozenset({"x"}))])
        self.assert_both({0: b}, {0: set()}, {0: set()})

    def test_single_block_use_before_def_is_live(self):
        # z = x + y —— x,y 向上暴露
        b = BasicBlock(0, [Instr(defs=frozenset({"z"}),
                                 uses=frozenset({"x", "y"}))])
        self.assert_both({0: b}, {0: {"x", "y"}}, {0: set()})

    def test_forward_only_chain(self):
        # b0 -> b1 -> b2
        blocks = {
            0: BasicBlock(0, [Instr(uses=frozenset({"a", "b"}))], succs=[1]),
            1: BasicBlock(1, [Instr(defs=frozenset({"c"}),
                                    uses=frozenset({"b"}))], succs=[2]),
            2: BasicBlock(2, [Instr(uses=frozenset({"c"}))]),
        }
        self.assert_both(
            blocks,
            {0: {"a", "b"}, 1: {"b"}, 2: {"c"}},
            {0: {"b"}, 1: {"c"}, 2: set()})

    def test_self_loop(self):
        # x = x + 1，自环
        b = BasicBlock(0, [Instr(defs=frozenset({"x"}), uses=frozenset({"x"}))],
                       succs=[0])
        self.assert_both({0: b}, {0: {"x"}}, {0: {"x"}})

    def test_two_block_loop(self):
        # header(uses n) -> body(n = n-1) -> header；header -> exit
        blocks = {
            0: BasicBlock(0, [Instr(uses=frozenset({"n"}))], succs=[1, 2]),
            1: BasicBlock(1, [Instr(defs=frozenset({"n"}),
                                    uses=frozenset({"n"}))], succs=[0]),
            2: BasicBlock(2),
        }
        self.assert_both(
            blocks,
            {0: {"n"}, 1: {"n"}, 2: set()},
            {0: {"n"}, 1: {"n"}, 2: set()})

    def test_unreachable_block(self):
        # 0 -> 1；块 2 不可达但仍需被分析
        blocks = {
            0: BasicBlock(0, [Instr(uses=frozenset({"a"}))], succs=[1]),
            1: BasicBlock(1),
            2: BasicBlock(2, [Instr(uses=frozenset({"u"}))], succs=[2]),
        }
        self.assert_both(
            blocks,
            {0: {"a"}, 1: set(), 2: {"u"}},
            {0: set(), 1: set(), 2: {"u"}})

    def test_exception_edge(self):
        # A 可能抛异常到 H；live_out[A] 必须包含 H 的 live_in
        blocks = {
            0: BasicBlock(0, [Instr(uses=frozenset({"x"}), may_throw=True)],
                          succs=[1], exc_succs=[2]),
            1: BasicBlock(1, [Instr(uses=frozenset({"y"}))]),
            2: BasicBlock(2, [Instr(uses=frozenset({"e"}))]),
        }
        self.assert_both(
            blocks,
            {0: {"x", "y", "e"}, 1: {"y"}, 2: {"e"}},
            {0: {"y", "e"}, 1: set(), 2: set()})

    def test_exception_only_edge(self):
        # 块只通过异常边把活跃性传出去
        blocks = {
            0: BasicBlock(0, [Instr(may_throw=True)], exc_succs=[1]),
            1: BasicBlock(1, [Instr(uses=frozenset({"e"}))]),
        }
        self.assert_both(blocks, {0: {"e"}, 1: {"e"}}, {0: {"e"}, 1: set()})

    def test_dead_definition_not_live(self):
        # x 定值后在后继被重新定值，中间无使用 -> x 不活跃
        blocks = {
            0: BasicBlock(0, [Instr(defs=frozenset({"x"}))], succs=[1]),
            1: BasicBlock(1, [Instr(defs=frozenset({"x"}))], succs=[2]),
            2: BasicBlock(2),
        }
        self.assert_both(blocks, {0: set(), 1: set(), 2: set()},
                         {0: set(), 1: set(), 2: set()})

    def test_nested_loops_small(self):
        # 深度 3 嵌套循环：两求解器一致，且循环变量贯穿各层
        blocks = gen_nested_loops(3, 2, n_vars=4, with_exceptions=True)
        r_fast = solve_liveness_worklist(blocks)
        r_slow = solve_liveness_naive(blocks)
        self.assertTrue(r_fast.same_sets(r_slow))
        # 出口块使用 v0，且处理器回边到最外层头 => v0 沿回路活跃到最外层
        # header 的出口（header 块自身定值 v0，故 v0 不在其入口活跃）
        self.assertIn("v0", r_fast.live_out[0])

    def test_diamond_forward_only(self):
        # 菱形汇合：仅前向边
        blocks = {
            0: BasicBlock(0, [Instr(uses=frozenset({"c"}))], succs=[1, 2]),
            1: BasicBlock(1, [Instr(defs=frozenset({"r"}),
                                    uses=frozenset({"c"}))], succs=[3]),
            2: BasicBlock(2, [Instr(defs=frozenset({"r"}))], succs=[3]),
            3: BasicBlock(3, [Instr(uses=frozenset({"r"}))]),
        }
        self.assert_both(
            blocks,
            {0: {"c"}, 1: {"c"}, 2: set(), 3: {"r"}},
            {0: {"c"}, 1: {"r"}, 2: {"r"}, 3: set()})


if __name__ == "__main__":
    unittest.main(verbosity=2)
