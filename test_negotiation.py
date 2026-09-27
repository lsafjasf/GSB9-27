"""negotiation 单元自测：规则示例 + 边界情形。运行：python3 -m unittest test_negotiation -v"""
import unittest

import negotiation
import reference


class RuleTests(unittest.TestCase):
    def test_specific_range_q_overrides_wildcard_q(self):
        # 具体优先于通配：text/html 的生效权重是显式的 0.1，而非通配的 0.9
        accept = "text/*;q=0.9, text/html;q=0.1"
        self.assertEqual(negotiation.select(accept, ["text/html", "text/plain"]),
                         "text/plain")

    def test_explicit_weight_respected(self):
        accept = "text/html;q=0.5, application/json;q=0.9"
        self.assertEqual(negotiation.select(accept, ["text/html", "application/json"]),
                         "application/json")

    def test_exact_beats_type_wildcard_on_tie(self):
        # q 相同，匹配范围更具体者胜
        accept = "text/*;q=0.5, text/html;q=0.5"
        self.assertEqual(negotiation.select(accept, ["text/plain", "text/html"]),
                         "text/html")

    def test_q_zero_excludes(self):
        accept = "*/*;q=1, text/html;q=0"
        self.assertEqual(negotiation.select(accept, ["text/html", "text/plain"]),
                         "text/plain")

    def test_params_must_match(self):
        accept = "text/html;level=1"
        self.assertEqual(negotiation.select(accept, ["text/html;level=2"]), None)
        self.assertEqual(
            negotiation.select(accept, ["text/html;level=1;charset=utf-8"]),
            "text/html;level=1;charset=utf-8")

    def test_more_params_more_specific(self):
        accept = "text/html;q=0.4, text/html;level=1;q=0.6"
        self.assertEqual(
            negotiation.select(accept, ["text/html;level=1", "text/html"]),
            "text/html;level=1")

    def test_client_order_breaks_tie(self):
        accept = "text/html, text/plain"
        self.assertEqual(negotiation.select(accept, ["text/plain", "text/html"]),
                         "text/html")

    def test_server_order_breaks_final_tie(self):
        accept = "text/*"
        self.assertEqual(negotiation.select(accept, ["text/plain", "text/html"]),
                         "text/plain")

    def test_invalid_q_drops_range(self):
        accept = "text/html;q=abc, text/*;q=0.5"
        self.assertEqual(negotiation.select(accept, ["text/html", "image/png"]),
                         "text/html")

    def test_type_case_insensitive(self):
        self.assertEqual(negotiation.select("TEXT/HTML", ["Text/Html"]), "Text/Html")


class EdgeCaseTests(unittest.TestCase):
    def test_all_wildcard(self):
        self.assertEqual(negotiation.select("*/*", ["text/html", "application/json"]),
                         "text/html")
        self.assertEqual(negotiation.select("*/*;q=0.3", ["a/b", "c/d"]), "a/b")

    def test_all_weights_zero(self):
        self.assertIsNone(negotiation.select("*/*;q=0", ["text/html"]))
        self.assertIsNone(
            negotiation.select("text/html;q=0, text/*;q=0.0, */*;q=0.000",
                               ["text/html", "text/plain", "image/png"]))

    def test_no_client_preference(self):
        self.assertEqual(negotiation.select(None, ["text/html", "text/plain"]),
                         "text/html")
        self.assertEqual(negotiation.select("", ["text/html"]), "text/html")
        self.assertEqual(negotiation.select("   ", ["text/html"]), "text/html")

    def test_empty_server_capabilities(self):
        self.assertIsNone(negotiation.select("text/html", []))
        self.assertIsNone(negotiation.select(None, []))
        self.assertIsNone(negotiation.select("*/*", []))

    def test_no_matching_range(self):
        self.assertIsNone(negotiation.select("image/png", ["text/html"]))


class DifferentialSmokeTests(unittest.TestCase):
    """自测内部再对拍一遍：所有上述用例两个实现必须一致。"""

    CASES = [
        ("text/*;q=0.9, text/html;q=0.1", ["text/html", "text/plain"]),
        ("text/html;q=0.5, application/json;q=0.9", ["text/html", "application/json"]),
        ("*/*;q=1, text/html;q=0", ["text/html", "text/plain"]),
        ("text/html;level=1", ["text/html;level=1;charset=utf-8", "text/html"]),
        ("*/*;q=0", ["text/html"]),
        (None, ["text/html", "text/plain"]),
        ("text/html", []),
        ("text/*", ["text/plain", "text/html"]),
    ]

    def test_two_implementations_agree(self):
        for header, variants in self.CASES:
            self.assertEqual(negotiation.select(header, variants),
                             reference.select(header, variants),
                             msg="header=%r variants=%r" % (header, variants))


if __name__ == "__main__":
    unittest.main()
