#!/usr/bin/env python3
"""内容协商边界用例自测（标准库 unittest）。

覆盖：
- 全是通配
- 权重全为零
- 客户端未提供偏好
- 服务端能力为空
- 具体优先于通配（含 q=0 精确拒绝）
- 参数化类型匹配
- 同权重并列的决胜顺序
"""

from __future__ import annotations

import unittest
from fractions import Fraction

import content_negotiation as cn


class EdgeCaseTests(unittest.TestCase):
    def test_all_wildcards(self):
        # 只有 */*：按服务端列表顺序选首选
        self.assertEqual(
            cn.best_match("*/*", ["text/plain", "text/html"]),
            "text/plain",
        )

    def test_all_wildcards_with_weight(self):
        # 全是通配且权重为 0.5：仍然有可接受项
        self.assertEqual(
            cn.best_match("*/*;q=0.5", ["application/json"]),
            "application/json",
        )

    def test_all_weights_zero(self):
        # 所有权重为 0：一切都被明确拒绝
        self.assertIsNone(
            cn.best_match("text/html;q=0, */*;q=0", ["text/html", "text/plain"])
        )

    def test_missing_preference_header_none(self):
        # 客户端没有提供 Accept（None）：服务端选自己的首选
        self.assertEqual(
            cn.best_match(None, ["application/json", "text/html"]),
            "application/json",
        )

    def test_missing_preference_header_empty(self):
        # 客户端提供的 Accept 为空字符串
        self.assertEqual(
            cn.best_match("   ", ["text/html"]),
            "text/html",
        )

    def test_missing_preference_all_unparsable(self):
        # 头部存在但全部无法解析：视为未提供偏好
        self.assertEqual(
            cn.best_match("garbage, ;, */html", ["text/html"]),
            "text/html",
        )

    def test_empty_server_capabilities(self):
        # 服务端没有任何能力
        self.assertIsNone(cn.best_match("text/html", []))
        self.assertIsNone(cn.best_match(None, []))
        self.assertIsNone(cn.best_match("*/*", []))

    def test_specific_beats_wildcard_on_weight(self):
        # 通配 q=0，精确类型 q=1：精确类型仍然可接受
        self.assertEqual(
            cn.best_match("*/*;q=0, text/html;q=1", ["text/html"]),
            "text/html",
        )
        # 顺序反过来也一样：具体度优先，与列表顺序无关
        self.assertEqual(
            cn.best_match("text/html;q=1, */*;q=0", ["text/html"]),
            "text/html",
        )

    def test_specific_q_zero_rejects_only_that_type(self):
        # 精确类型 q=0、通配 q=1：该类型被拒绝，其他类型仍可接受
        self.assertIsNone(
            cn.best_match("text/html;q=0, */*;q=1", ["text/html"])
        )
        self.assertEqual(
            cn.best_match(
                "text/html;q=0, */*;q=1", ["text/html", "text/plain"]
            ),
            "text/plain",
        )

    def test_subtype_wildcard_vs_full_wildcard(self):
        self.assertEqual(
            cn.best_match(
                "*/*;q=0.1, text/*;q=0.9, text/html;q=0.2",
                ["text/html", "image/png"],
            ),
            # text/html 的有效权重由最具体的 text/html 条目决定 -> 0.2；
            # image/png 只命中 */* -> 0.1。0.2 > 0.1，选 text/html。
            "text/html",
        )

    def test_parameterized_match(self):
        # 带参数的精确条目必须参数完全相等才算匹配
        header = "text/html;level=1;q=0.9, text/html;q=0.5"
        self.assertEqual(
            cn.best_match(header, ["text/html;level=1"]), "text/html;level=1"
        )
        # level=2 的能力不命中带参数条目，回退到无参数条目
        self.assertEqual(
            cn.best_match(header, ["text/html;level=2"]), "text/html;level=2"
        )

    def test_tie_broken_by_server_order(self):
        # q 与具体度都相同：服务端列表靠前的能力胜出
        self.assertEqual(
            cn.best_match("text/*;q=1", ["text/html", "text/plain"]),
            "text/html",
        )

    def test_client_order_tiebreak_for_equal_specificity(self):
        # 同样具体的重复条目：靠前的条目决定有效权重
        raw, q, decision = cn.best_match_with_reason(
            "text/*;q=0.3, text/*;q=0.9", ["text/html"]
        )
        self.assertEqual(raw, "text/html")
        self.assertEqual(q, Fraction("0.3"))

    def test_higher_weight_wins_over_specificity(self):
        # 显式权重优先：精确类型权重低时，泛化类型权重高者胜
        self.assertEqual(
            cn.best_match(
                "text/html;q=0.1, text/*;q=0.9",
                ["text/html", "text/plain"],
            ),
            "text/plain",
        )

    def test_case_insensitive_type(self):
        self.assertEqual(
            cn.best_match("TEXT/HTML;Q=0.8", ["Text/Html"]), "Text/Html"
        )

    def test_invalid_q_drops_entry(self):
        # q 越界的条目丢弃；其后的通配仍生效
        self.assertEqual(
            cn.best_match("text/html;q=2, */*;q=0.1", ["image/png"]),
            "image/png",
        )


if __name__ == "__main__":
    unittest.main()
