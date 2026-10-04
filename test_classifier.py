"""自测：覆盖规则一致、规则冲突、字段名误导、同一字段多级及边界情形。"""
import json
import unittest
from pathlib import Path

from dataclassify import Classifier
from dataclassify.engine import mask
from dataclassify.levels import level_rank
from dataclassify.rules import DEFAULT_LEVEL, luhn_ok

DATA_DIR = Path(__file__).parent / "data"


class ScenarioTest(unittest.TestCase):
    """四类核心情形。"""

    def setUp(self):
        self.clf = Classifier()

    def test_consistent_rules(self):
        """规则一致：字段名与取值证据指向同一级别，无冲突。"""
        res = self.clf.classify(
            "mobile", ["13812345678", "15998765432"], source="CRM"
        )
        self.assertEqual(res.final_level, "L3")
        self.assertFalse(res.has_conflict)
        self.assertIn("value", res.evidences[0].matched_by)

    def test_conflicting_rules_resolved_by_priority(self):
        """规则冲突：邮箱(L3)与身份证(L4)同时命中，按优先级裁决为 L4。"""
        res = self.clf.classify(
            "remark",
            ["11010519491231002X", "a.b@example.com", "b.c@example.com"],
        )
        self.assertEqual(res.final_level, "L4")
        self.assertTrue(res.has_conflict)
        loser_rules = {c.loser_rule for c in res.conflicts}
        self.assertIn("R-EMAIL-VALUE", loser_rules)
        for conflict in res.conflicts:
            self.assertEqual(conflict.winner_rule, "R-ID-VALUE")
            self.assertIn("优先级", conflict.reason)

    def test_misleading_field_name(self):
        """字段名误导：名为 user_idcard，取值实为工号，取值证据胜出。"""
        res = self.clf.classify("user_idcard", ["E0001", "E0023", "E1042"])
        self.assertEqual(res.final_level, "L2")
        self.assertTrue(res.has_conflict)
        levels = {e.rule_id: e.level for e in res.evidences}
        self.assertEqual(levels["R-ID-NAME"], "L4")
        self.assertEqual(levels["R-EMP-CODE-VALUE"], "L2")

    def test_multi_level_same_field(self):
        """同一字段多级：备注字段同时含身份证与邮箱，就高定级并列出冲突。"""
        res = self.clf.classify(
            "remark",
            ["11010519491231002X", "310110199001011234",
             "a.b@example.com", "lisi@mail.cn", "普通文本"],
        )
        self.assertEqual(res.final_level, "L4")
        hit_levels = {e.level for e in res.evidences}
        self.assertIn("L4", hit_levels)
        self.assertIn("L3", hit_levels)
        self.assertTrue(res.has_conflict)

    def test_source_downgrades_public_info(self):
        """来源证据：官网公开页的地址字段降为公开级。"""
        res = self.clf.classify(
            "company_address", ["北京市海淀区中关村大街1号"], source="官网公开页"
        )
        self.assertEqual(res.final_level, "L1")
        self.assertTrue(res.has_conflict)  # 字段名推 L3，来源证据胜出


class EdgeCaseTest(unittest.TestCase):
    """边界用例。"""

    def setUp(self):
        self.clf = Classifier()

    def test_no_rule_hits_default_level(self):
        res = self.clf.classify("score", ["87", "92"])
        self.assertEqual(res.final_level, DEFAULT_LEVEL)
        self.assertEqual(res.evidences, [])
        self.assertIn("兜底", res.reason)

    def test_empty_samples_name_only(self):
        res = self.clf.classify("real_name", [])
        self.assertEqual(res.final_level, "L3")
        self.assertEqual(res.evidences[0].matched_by, ["field_name"])

    def test_value_threshold_boundary(self):
        """1/3 命中（阈值 30%）判命中；阈值调到 50% 则不命中。"""
        samples = ["11010519491231002X", "N/A", "test", ""]
        res = self.clf.classify("memo", samples)
        self.assertEqual(res.final_level, "L4")
        strict = Classifier(
            rules=[r for r in self.clf.rules])
        # 用自定义阈值重建规则验证边界
        from dataclasses import replace
        strict_rules = [
            replace(r, value_threshold=0.5) if r.rule_id == "R-ID-VALUE" else r
            for r in strict.rules
        ]
        res2 = Classifier(rules=strict_rules).classify("memo", samples)
        self.assertNotIn("R-ID-VALUE", {e.rule_id for e in res2.evidences})

    def test_none_and_blank_samples_ignored(self):
        res = self.clf.classify("memo", [None, "", "   "])
        self.assertEqual(res.final_level, DEFAULT_LEVEL)

    def test_bank_card_requires_luhn(self):
        """16-19 位数字未过 Luhn 不命中银行卡规则。"""
        res = self.clf.classify("card_no", ["6225880123456789"])  # Luhn 不通过
        self.assertNotIn("R-BANK-VALUE", {e.rule_id for e in res.evidences})
        res_ok = self.clf.classify("card_no", ["6225880123456783"])
        self.assertEqual(res_ok.final_level, "L4")

    def test_unknown_level_rank(self):
        self.assertEqual(level_rank("L9"), -1)

    def test_mask(self):
        self.assertEqual(mask("11010519491231002X"), "110***2X")
        self.assertEqual(mask("abc"), "a***")
        self.assertEqual(mask(""), "")

    def test_luhn(self):
        self.assertTrue(luhn_ok("6222020200011111222"))
        self.assertFalse(luhn_ok("6222020200011111223"))
        self.assertFalse(luhn_ok("abc"))

    def test_same_priority_higher_level_wins(self):
        """同优先级冲突时就高。"""
        from dataclasses import replace
        from dataclassify.rules import DEFAULT_RULES
        rules = [
            replace(r, priority=55) if r.rule_id == "R-ID-NAME" else r
            for r in DEFAULT_RULES
        ]
        res = Classifier(rules=rules).classify(
            "id_card_full_name", ["张伟", "李四"]  # 命中 R-ID-NAME 与 R-NAME-PII，均 P55
        )
        self.assertEqual(res.final_level, "L4")
        self.assertTrue(any("就高" in c.reason for c in res.conflicts))


class ReconcileTest(unittest.TestCase):
    """与人工复核样例对拍。"""

    def test_matches_human_review(self):
        fields = json.loads((DATA_DIR / "fields.json").read_text(encoding="utf-8"))["fields"]
        reviewed = json.loads((DATA_DIR / "reviewed.json").read_text(encoding="utf-8"))["reviewed"]
        clf = Classifier()
        mismatches = []
        for item in fields:
            res = clf.classify(item["name"], item.get("samples"), item.get("source", ""))
            expect = reviewed[item["id"]]["level"]
            if res.final_level != expect:
                mismatches.append((item["name"], res.final_level, expect))
        self.assertEqual(mismatches, [])


if __name__ == "__main__":
    unittest.main()
