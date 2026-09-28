"""迁移报告与往返一致性的回归测试。

运行: python3 -m unittest discover -s tests -v
"""

import unittest

from dataformat import (
    ReportMismatchError,
    build_report,
    downgrade,
    roundtrip_diff,
    upgrade,
    verify_report,
)


def make_v1():
    return {
        "version": 1,
        "name": "order-service",
        "timeout": 30,  # 秒
        "server": {"host": "db.internal"},  # port 缺失 -> defaulted
        "owner": "team-pay",  # 未知字段
    }


def entries_by_path(report):
    return {e["path"]: e for e in report["fields"]}


class BuildReport(unittest.TestCase):
    def setUp(self):
        self.doc = make_v1()
        self.result = upgrade(self.doc, 1, 3)
        self.report = build_report(self.doc, 1, 3, result=self.result)
        self.entries = entries_by_path(self.report)

    def test_renamed_entry(self):
        e = self.entries["timeout"]
        self.assertEqual(e["action"], "renamed")
        self.assertEqual(e["to"], "timeout_ms")
        self.assertEqual(e["transform"], "_seconds_to_ms")
        self.assertEqual(e["value_before"], 30)
        self.assertEqual(e["value_after"], 30000)

    def test_removed_entry(self):
        e = self.entries["name"]
        self.assertEqual(e["action"], "removed")
        self.assertEqual(e["archived_to"], "_meta.removed.name")
        self.assertEqual(e["value_before"], "order-service")

    def test_defaulted_entries(self):
        self.assertEqual(self.entries["server.port"]["action"], "defaulted")
        self.assertEqual(self.entries["server.port"]["value_after"], 80)
        self.assertEqual(self.entries["server.retries"]["action"], "defaulted")
        self.assertEqual(self.entries["tags"]["action"], "defaulted")
        self.assertEqual(self.entries["tags"]["value_after"], [])
        for path in ("server.port", "server.retries", "tags"):
            self.assertEqual(self.entries[path]["source"], "schema_default")

    def test_kept_and_preserved_unknown(self):
        self.assertEqual(self.entries["server.host"]["action"], "kept")
        e = self.entries["owner"]
        self.assertEqual(e["action"], "preserved_unknown")
        self.assertEqual(e["value_after"], "team-pay")

    def test_version_bump_entry(self):
        e = self.entries["version"]
        self.assertEqual(e["action"], "version_bump")
        self.assertEqual((e["value_before"], e["value_after"]), (1, 3))

    def test_summary_counts(self):
        s = self.report["summary"]
        self.assertEqual(s["renamed"], 1)
        self.assertEqual(s["removed"], 1)
        self.assertEqual(s["defaulted"], 3)
        self.assertEqual(s["kept"], 1)
        self.assertEqual(s["preserved_unknown"], 1)


class VerifyReport(unittest.TestCase):
    def setUp(self):
        self.doc = make_v1()
        self.result = upgrade(self.doc, 1, 3)
        self.report = build_report(self.doc, 1, 3, result=self.result)

    def test_consistent_report_passes(self):
        self.assertTrue(verify_report(self.report, self.doc, self.result))

    def test_tampered_result_detected(self):
        tampered = upgrade(self.doc, 1, 3)
        tampered["timeout_ms"] = 9999
        with self.assertRaises(ReportMismatchError):
            verify_report(self.report, self.doc, tampered)

    def test_tampered_report_detected(self):
        bad = build_report(self.doc, 1, 3, result=self.result)
        for e in bad["fields"]:
            if e["path"] == "tags":
                e["value_after"] = ["forged"]
        with self.assertRaises(ReportMismatchError):
            verify_report(bad, self.doc, self.result)

    def test_missing_entry_detected(self):
        """完备性：漏报任何一个字段都必须被发现。"""
        incomplete = build_report(self.doc, 1, 3, result=self.result)
        incomplete["fields"] = [
            e for e in incomplete["fields"] if e["path"] != "owner"
        ]
        with self.assertRaises(ReportMismatchError):
            verify_report(incomplete, self.doc, self.result)

    def test_report_without_result_argument_is_consistent(self):
        report = build_report(self.doc, 1, 3)
        self.assertTrue(verify_report(report, self.doc, upgrade(self.doc, 1, 3)))


class RoundtripConsistency(unittest.TestCase):
    def test_roundtrip_diff_is_superset_only(self):
        back, diffs = roundtrip_diff(make_v1(), 1, 3)
        kinds = {d["kind"] for d in diffs}
        self.assertLessEqual(kinds, {"added"})
        added = {d["path"] for d in diffs}
        self.assertEqual(added, {"server.port", "server.retries", "tags"})

    def test_roundtrip_preserves_all_original_fields(self):
        doc = make_v1()
        back, _ = roundtrip_diff(doc, 1, 3)
        self.assertEqual(back["name"], doc["name"])
        self.assertEqual(back["timeout"], doc["timeout"])
        self.assertEqual(back["owner"], doc["owner"])
        self.assertEqual(back["server"]["host"], doc["server"]["host"])

    def test_roundtrip_superset_fields_survive_reupgrade(self):
        """往返多出的字段再升级时不丢，且不再被标记为 defaulted。"""
        back, _ = roundtrip_diff(make_v1(), 1, 3)
        re_up = upgrade(back, 1, 3)
        self.assertEqual(re_up["server"]["retries"], 3)
        self.assertEqual(re_up["tags"], [])
        self.assertNotIn("tags", re_up["_meta"]["defaulted"])
        self.assertNotIn("server.retries", re_up["_meta"]["defaulted"])

    def test_downgrade_then_upgrade_value_stability(self):
        """timeout 30s -> 30000ms -> 30s：整除路径保持 int。"""
        back = downgrade(upgrade(make_v1(), 1, 3), 3, 1)
        self.assertEqual(back["timeout"], 30)
        self.assertIsInstance(back["timeout"], int)


if __name__ == "__main__":
    unittest.main()
