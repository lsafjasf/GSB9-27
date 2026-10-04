import json
import os
import shutil
import tempfile
import unittest

from precheck.checks import PASS
from precheck.cli import build_report
from precheck.runner import render

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_repo_copy():
    tmp = tempfile.mkdtemp(prefix="precheck-test-")
    shutil.copytree(os.path.join(REPO_ROOT, "demo"), os.path.join(tmp, "demo"))
    shutil.copy2(os.path.join(REPO_ROOT, "precheck.json"), os.path.join(tmp, "precheck.json"))
    return tmp


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.root = make_repo_copy()
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, rel, content):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def test_full_run_on_clean_repo_passes(self):
        report = build_report(self.root, force_full=True)
        self.assertEqual(report.verdict, PASS)
        self.assertEqual(len(report.results), 5)

    def test_incremental_skips_unaffected_checks(self):
        report = build_report(self.root, changed=["demo/data/users.json"])
        self.assertEqual(report.affected, ["json-validate"])
        self.assertEqual(report.skipped,
                         ["py-syntax", "py-style", "py-tests", "config-validate"])

    def test_style_fault_fails_py_style_only(self):
        self.write("demo/src/calculator.py", "x = 1\n" + "y = '" + "z" * 200 + "'\n")
        report = build_report(self.root, changed=["demo/src/calculator.py"])
        by_name = {r.name: r for r in report.results}
        self.assertEqual(by_name["py-style"].status, "FAIL")
        self.assertEqual(by_name["py-syntax"].status, PASS)
        self.assertEqual(report.verdict, "FAIL")

    def test_syntax_fault_fails_py_syntax(self):
        self.write("demo/src/strings.py", "def broken(:\n    pass\n")
        report = build_report(self.root, changed=["demo/src/strings.py"])
        by_name = {r.name: r for r in report.results}
        self.assertEqual(by_name["py-syntax"].status, "FAIL")
        self.assertTrue(any("strings.py:1" in d for d in by_name["py-syntax"].details))

    def test_failing_test_fails_py_tests(self):
        self.write(
            "demo/tests/test_calculator.py",
            "import unittest\nimport calculator\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_wrong(self):\n"
            "        self.assertEqual(calculator.add(1, 1), 3)\n",
        )
        report = build_report(self.root, changed=["demo/tests/test_calculator.py"])
        by_name = {r.name: r for r in report.results}
        self.assertEqual(by_name["py-tests"].status, "FAIL")
        self.assertTrue(any("AssertionError" in d for d in by_name["py-tests"].details))

    def test_broken_json_fails_json_validate(self):
        self.write("demo/data/users.json", "[{broken\n")
        report = build_report(self.root, changed=["demo/data/users.json"])
        self.assertEqual(report.results[0].name, "json-validate")
        self.assertEqual(report.results[0].status, "FAIL")

    def test_config_change_falls_back_to_full(self):
        self.write("precheck.json", json.dumps({
            "version": 1,
            "ignore_patterns": ["**.md"],
            "config_files": ["precheck.json"],
            "checks": [{"name": "py-syntax", "patterns": ["demo/src/**.py"]}],
        }))
        report = build_report(self.root, changed=["precheck.json"])
        self.assertEqual(report.mode, "incremental->full")
        self.assertTrue(any("CONFIG_CHANGED" in r for r in report.fallback_reasons))
        self.assertEqual(len(report.results), 1)  # new config defines a single check

    def test_unknown_file_falls_back_to_full(self):
        report = build_report(self.root, changed=["demo/assets/logo.png"])
        self.assertEqual(report.mode, "incremental->full")
        self.assertTrue(any("UNKNOWN_IMPACT" in r for r in report.fallback_reasons))
        self.assertEqual(len(report.results), 5)

    def test_invalid_config_falls_back_to_full_and_flags_error(self):
        self.write("precheck.json", "{ not json !!!")
        report = build_report(self.root, changed=["demo/src/calculator.py"])
        self.assertEqual(report.mode, "incremental->full")
        self.assertTrue(any("CONFIG_INVALID" in r for r in report.fallback_reasons))
        by_name = {r.name: r for r in report.results}
        self.assertEqual(by_name["config-validate"].status, "FAIL")
        self.assertEqual(report.verdict, "FAIL")

    def test_report_render_is_deterministic(self):
        first = render(build_report(self.root, force_full=True))
        second = render(build_report(self.root, force_full=True))
        strip = lambda text: "\n".join(
            line for line in text.splitlines() if "ms" not in line
        )
        self.assertEqual(strip(first), strip(second))


if __name__ == "__main__":
    unittest.main()
