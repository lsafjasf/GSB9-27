"""dq 库自测：python -m unittest discover tests -v"""
from __future__ import annotations

import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from dq.drift import build_baseline, save_baseline
from dq.engine import run_checks

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def make_baseline_file(values, field="x", kind="numeric", max_age_days=30,
                       created_at=None):
    baseline = build_baseline(values, field=field, kind=kind,
                              max_age_days=max_age_days,
                              now=created_at or NOW)
    path = os.path.join(tempfile.mkdtemp(), f"{field}.baseline.json")
    save_baseline(baseline, path)
    return path


class TestCompleteness(unittest.TestCase):
    def test_missing_ratio_and_samples(self):
        rows = [{"a": 1}, {"a": None}, {"a": ""}, {"b": 2}, {"a": 3}]
        report = run_checks(rows, {"rules": [
            {"id": "c", "type": "completeness", "field": "a"}]})
        result = report.results[0]
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.hit_count, 3)
        self.assertAlmostEqual(result.hit_ratio, 0.6)
        self.assertEqual([s["row"] for s in result.samples], [1, 2, 3])
        self.assertIn("a", result.explanation)

    def test_all_fields_missing(self):
        rows = [{"a": None}, {"a": ""}, {}]
        report = run_checks(rows, {"rules": [
            {"id": "c", "type": "completeness", "field": "a"}]})
        result = report.results[0]
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.hit_ratio, 1.0)


class TestUniqueness(unittest.TestCase):
    def test_duplicates(self):
        rows = [{"id": 1}, {"id": 2}, {"id": 1}, {"id": 3}, {"id": 2}]
        report = run_checks(rows, {"rules": [
            {"id": "u", "type": "uniqueness", "fields": ["id"]}]})
        result = report.results[0]
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.hit_count, 2)
        self.assertEqual(result.samples[0]["row"], 2)
        self.assertIn("第 0 行", result.samples[0]["reason"])

    def test_composite_key(self):
        rows = [{"a": 1, "b": 1}, {"a": 1, "b": 2}, {"a": 1, "b": 1}]
        report = run_checks(rows, {"rules": [
            {"id": "u", "type": "uniqueness", "fields": ["a", "b"]}]})
        self.assertEqual(report.results[0].hit_count, 1)


class TestRange(unittest.TestCase):
    def test_all_out_of_range(self):
        rows = [{"age": 200}, {"age": -5}, {"age": 999}]
        report = run_checks(rows, {"rules": [
            {"id": "r", "type": "range", "field": "age", "min": 0, "max": 120}]})
        result = report.results[0]
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.hit_ratio, 1.0)
        self.assertEqual(len(result.samples), 3)

    def test_non_numeric_counts_as_violation(self):
        rows = [{"age": "abc"}, {"age": 30}]
        report = run_checks(rows, {"rules": [
            {"id": "r", "type": "range", "field": "age", "min": 0, "max": 120}]})
        result = report.results[0]
        self.assertEqual(result.hit_count, 1)
        self.assertIn("非数值", result.samples[0]["reason"])

    def test_missing_skipped_not_violation(self):
        rows = [{"age": None}, {"age": 30}]
        report = run_checks(rows, {"rules": [
            {"id": "r", "type": "range", "field": "age", "min": 0, "max": 120}]})
        self.assertEqual(report.results[0].status, "pass")


class TestTypeConsistency(unittest.TestCase):
    def test_mismatch(self):
        rows = [{"score": 1.5}, {"score": "bad"}, {"score": 2}]
        report = run_checks(rows, {"rules": [
            {"id": "t", "type": "type_consistency", "field": "score",
             "expected": "number"}]})
        result = report.results[0]
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.hit_count, 1)
        self.assertIn("str", result.samples[0]["reason"])

    def test_bool_is_not_int(self):
        rows = [{"flag": True}, {"flag": 1}]
        report = run_checks(rows, {"rules": [
            {"id": "t", "type": "type_consistency", "field": "flag",
             "expected": "int"}]})
        self.assertEqual(report.results[0].hit_count, 1)


class TestDrift(unittest.TestCase):
    def setUp(self):
        rng = random.Random(1)
        self.ref = [rng.gauss(50, 10) for _ in range(5000)]
        self.baseline_path = make_baseline_file(self.ref)

    def _drift_rules(self):
        return {"rules": [{"id": "d", "type": "drift", "field": "x",
                           "baseline": self.baseline_path,
                           "psi_threshold": 0.25}]}

    def test_stable_distribution_passes(self):
        rng = random.Random(2)
        rows = [{"x": rng.gauss(50, 10)} for _ in range(2000)]
        report = run_checks(rows, self._drift_rules(), now=NOW)
        result = report.results[0]
        self.assertEqual(result.status, "pass")
        self.assertLess(result.details["psi"], 0.25)

    def test_shifted_distribution_fails_and_explains(self):
        rng = random.Random(3)
        rows = [{"x": rng.gauss(90, 10)} for _ in range(2000)]
        report = run_checks(rows, self._drift_rules(), now=NOW)
        result = report.results[0]
        self.assertEqual(result.status, "fail")
        self.assertGreater(result.details["psi"], 0.25)
        self.assertTrue(result.samples)
        self.assertIn("漂移分箱", result.samples[0]["reason"])
        self.assertIn("top_bins", result.details)

    def test_bin_edges_frozen(self):
        """新数据范围再宽也不改变基线边界与判定标准。"""
        with open(self.baseline_path) as fh:
            before = fh.read()
        rows = [{"x": v} for v in [-10**6, 10**6, 0, 50]]
        run_checks(rows, self._drift_rules(), now=NOW)
        with open(self.baseline_path) as fh:
            after = fh.read()
        self.assertEqual(before, after)

    def test_stale_baseline(self):
        stale_path = make_baseline_file(
            self.ref, max_age_days=7,
            created_at=NOW - timedelta(days=30))
        rules = {"rules": [{"id": "d", "type": "drift", "field": "x",
                            "baseline": stale_path}]}
        report = run_checks([{"x": 1}], rules, now=NOW)
        result = report.results[0]
        self.assertEqual(result.status, "error")
        self.assertIn("过期", result.explanation)

    def test_categorical_drift(self):
        path = make_baseline_file(["a"] * 90 + ["b"] * 10, field="ch",
                                  kind="categorical")
        rules = {"rules": [{"id": "d", "type": "drift", "field": "ch",
                            "baseline": path, "psi_threshold": 0.25}]}
        rows = [{"ch": "b"}] * 90 + [{"ch": "a"}] * 10
        report = run_checks(rows, rules, now=NOW)
        self.assertEqual(report.results[0].status, "fail")


class TestEdgeCases(unittest.TestCase):
    def test_empty_table(self):
        path = make_baseline_file([1, 2, 3, 4, 5])
        report = run_checks([], {"rules": [
            {"id": "c", "type": "completeness", "field": "a"},
            {"id": "u", "type": "uniqueness", "fields": ["a"]},
            {"id": "r", "type": "range", "field": "a", "min": 0, "max": 1},
            {"id": "t", "type": "type_consistency", "field": "a", "expected": "int"},
            {"id": "d", "type": "drift", "field": "a", "baseline": path},
        ]}, now=NOW)
        self.assertEqual(report.total_rows, 0)
        for result in report.results:
            self.assertEqual(result.status, "no_data")
        self.assertEqual(report.status, "no_data")

    def test_all_missing_other_checks_no_data(self):
        path = make_baseline_file([1, 2, 3])
        rows = [{"a": None}, {"a": None}]
        report = run_checks(rows, {"rules": [
            {"id": "c", "type": "completeness", "field": "a"},
            {"id": "r", "type": "range", "field": "a", "min": 0, "max": 1},
            {"id": "t", "type": "type_consistency", "field": "a", "expected": "int"},
            {"id": "d", "type": "drift", "field": "a", "baseline": path},
        ]}, now=NOW)
        statuses = {r.rule_type: r.status for r in report.results}
        self.assertEqual(statuses["completeness"], "fail")
        self.assertEqual(statuses["range"], "no_data")
        self.assertEqual(statuses["type_consistency"], "no_data")
        self.assertEqual(statuses["drift"], "no_data")


class TestCombinationAndReport(unittest.TestCase):
    def test_rules_combine_and_report_serializable(self):
        path = make_baseline_file([10, 20, 30, 40, 50])
        rows = [
            {"id": 1, "age": 30, "score": 0.5},
            {"id": 1, "age": 300, "score": "bad"},
            {"id": 2, "age": None, "score": 0.7},
        ]
        report = run_checks(rows, {"rules": [
            {"id": "c1", "type": "completeness", "field": "age"},
            {"id": "u1", "type": "uniqueness", "fields": ["id"]},
            {"id": "r1", "type": "range", "field": "age", "min": 0, "max": 120},
            {"id": "t1", "type": "type_consistency", "field": "score",
             "expected": "number"},
            {"id": "d1", "type": "drift", "field": "age", "baseline": path},
        ]}, now=NOW)
        self.assertEqual(len(report.results), 5)
        self.assertEqual(report.status, "fail")
        parsed = json.loads(report.to_json())
        self.assertEqual(parsed["status"], "fail")
        self.assertEqual(parsed["total_rows"], 3)
        for check in parsed["checks"]:
            self.assertIn("explanation", check)
            self.assertIn("samples", check)
            self.assertIn("hit_ratio", check)

    def test_empty_rules_rejected(self):
        with self.assertRaises(ValueError):
            run_checks([{"a": 1}], {"rules": []})


if __name__ == "__main__":
    unittest.main()
