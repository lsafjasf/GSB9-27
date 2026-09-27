#!/usr/bin/env python3
"""baseline_check 自测：python3 -m unittest discover -s tests -v"""
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import baseline_check as bc

ROOT = os.path.join(os.path.dirname(__file__), "..")


def make_baseline(**overrides):
    baseline = {
        "name": "test-baseline",
        "version": "2.0.0",
        "valid_until": "2026-12-31",
        "rules": [
            {"id": "r1", "key": "a.b", "requirement": "a.b 必须为 true",
             "op": "eq", "expect": True, "severity": "critical",
             "since": "1.0.0", "remediation": "设为 true"},
            {"id": "r2", "key": "c", "requirement": "c 不得小于 10",
             "op": "ge", "expect": 10, "severity": "warning",
             "since": "2.0.0", "remediation": "调大到 >= 10"},
            {"id": "r3", "key": "d", "requirement": "d 必须为 on",
             "op": "eq", "expect": "on", "severity": "error",
             "since": "2.0.0", "remediation": "补上 d=on"},
        ],
    }
    baseline.update(overrides)
    return baseline


class MergeTest(unittest.TestCase):
    def test_deep_merge_and_provenance_order(self):
        merged, prov = bc.merge_sources([
            ("low", {"a": {"b": 1, "x": 1}}),
            ("high", {"a": {"b": 2}}),
        ])
        self.assertEqual(merged, {"a": {"b": 2, "x": 1}})
        self.assertEqual(prov[("a", "b")], [("low", 1), ("high", 2)])
        self.assertEqual(prov[("a", "x")], [("low", 1)])

    def test_scalar_overrides_subtree_invalidates_old_provenance(self):
        merged, prov = bc.merge_sources([
            ("low", {"a": {"b": 1}}),
            ("high", {"a": 9}),
        ])
        self.assertEqual(merged, {"a": 9})
        self.assertNotIn(("a", "b"), prov)
        self.assertEqual(prov[("a",)], [("high", 9)])

    def test_subtree_merges_into_scalar(self):
        merged, prov = bc.merge_sources([
            ("low", {"a": 9}),
            ("high", {"a": {"b": 1}}),
        ])
        self.assertEqual(merged, {"a": {"b": 1}})
        self.assertEqual(prov[("a", "b")], [("high", 1)])


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.today = datetime.date(2026, 9, 28)

    def evaluate(self, sources, **baseline_overrides):
        merged, prov = bc.merge_sources(sources)
        return bc.evaluate(make_baseline(**baseline_overrides), merged, prov, self.today)

    def test_judges_only_final_effective_value(self):
        # 低优先级违规、高优先级修复 => 必须判 PASS（不得对单个文件下结论）
        findings, _ = self.evaluate([("low", {"a": {"b": False}, "c": 99, "d": "on"}),
                                     ("high", {"a": {"b": True}})])
        by_id = {f["id"]: f for f in findings}
        self.assertEqual(by_id["r1"]["status"], "PASS")
        self.assertEqual(by_id["r1"]["actual"], True)

    def test_conflict_high_priority_wins_and_chain_recorded(self):
        # 高优先级把值改回违规 => FAIL，且来源链完整
        findings, _ = self.evaluate([("low", {"a": {"b": True}, "c": 99, "d": "on"}),
                                     ("high", {"a": {"b": False}})])
        r1 = {f["id"]: f for f in findings}["r1"]
        self.assertEqual(r1["status"], "FAIL")
        self.assertEqual(r1["chain"], [("low", True), ("high", False)])

    def test_missing_field(self):
        findings, _ = self.evaluate([("only", {"a": {"b": True}, "c": 99})])
        r3 = {f["id"]: f for f in findings}["r3"]
        self.assertEqual(r3["status"], "MISSING")
        self.assertEqual(r3["severity"], "error")

    def test_new_vs_legacy_classification(self):
        # r1 since=1.0.0 < 基线 2.0.0 => 历史存量；r2/r3 since=2.0.0 => 新引入
        findings, _ = self.evaluate([("only", {"a": {"b": False}, "c": 1})])
        by_id = {f["id"]: f for f in findings}
        self.assertEqual(by_id["r1"]["category"], "历史存量")
        self.assertEqual(by_id["r2"]["category"], "新引入")
        self.assertEqual(by_id["r3"]["category"], "新引入")

    def test_baseline_expired(self):
        _, expired = self.evaluate([("only", {})], valid_until="2026-06-30")
        self.assertIsNotNone(expired)
        self.assertEqual(expired["days_overdue"], 90)

    def test_baseline_not_expired(self):
        _, expired = self.evaluate([("only", {})], valid_until="2026-12-31")
        self.assertIsNone(expired)

    def test_type_mismatch_fails(self):
        findings, _ = self.evaluate([("only", {"a": {"b": True}, "c": "abc", "d": "on"})])
        r2 = {f["id"]: f for f in findings}["r2"]
        self.assertEqual(r2["status"], "FAIL")


class CliTest(unittest.TestCase):
    def run_cli(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = bc.main(argv)
        return code, out.getvalue()

    def write_tmp(self, obj):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        self.addCleanup(os.unlink, path)
        return path

    def test_exit_code_and_report_fields(self):
        baseline = self.write_tmp(make_baseline())
        low = self.write_tmp({"a": {"b": False}, "c": 99, "d": "on"})
        high = self.write_tmp({"a": {"b": True}})
        code, report = self.run_cli([
            "--baseline", baseline, "--source", f"low={low}",
            "--source", f"high={high}", "--now", "2026-09-28"])
        self.assertEqual(code, 0)  # 仅剩 warning 级以下问题时不阻断
        self.assertIn("通过项", report)

    def test_exit_code_1_on_critical(self):
        baseline = self.write_tmp(make_baseline())
        src = self.write_tmp({"a": {"b": False}, "c": 99, "d": "on"})
        code, report = self.run_cli([
            "--baseline", baseline, "--source", f"s={src}", "--now", "2026-09-28"])
        self.assertEqual(code, 1)
        self.assertIn("[FAIL][critical][历史存量] a.b", report)
        self.assertIn("修复建议", report)
        self.assertIn("来源链", report)

    def test_json_output(self):
        baseline = self.write_tmp(make_baseline())
        src = self.write_tmp({"a": {"b": True}, "c": 99, "d": "on"})
        code, report = self.run_cli([
            "--baseline", baseline, "--source", f"s={src}",
            "--now", "2026-09-28", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(report)
        self.assertEqual(data["baseline"]["version"], "2.0.0")
        self.assertTrue(all(f["status"] == "PASS" for f in data["findings"]))


class SampleReportTest(unittest.TestCase):
    """样例数据 + 提交的报告样例必须与脚本实际输出一致（人工核对基准）。"""

    def test_sample_report_matches_script_output(self):
        with open(os.path.join(ROOT, "examples", "report.sample.txt"),
                  encoding="utf-8") as fh:
            expected = fh.read()
        out = io.StringIO()
        with redirect_stdout(out):
            code = bc.main([
                "--baseline", os.path.join(ROOT, "baseline.json"),
                "--source", f"defaults={ROOT}/examples/sources/defaults.json",
                "--source", f"system={ROOT}/examples/sources/system.json",
                "--source", f"app={ROOT}/examples/sources/app.json",
                "--source", f"local={ROOT}/examples/sources/local.json",
                "--now", "2026-09-28",
            ])
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), expected)

    def test_sample_covers_required_scenarios(self):
        with open(os.path.join(ROOT, "examples", "report.sample.txt"),
                  encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("基线已过期", report)                       # 基线过期
        self.assertIn("local=true (生效)", report)                # 多来源冲突
        self.assertIn("[MISSING]", report)                        # 字段缺失
        self.assertIn("新引入", report)                           # 新旧分类
        self.assertIn("历史存量", report)


if __name__ == "__main__":
    unittest.main()
