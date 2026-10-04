"""End-to-end proof that incremental results equal full results.

For each change scenario we copy the repo to a temp dir, apply the mutation,
and run `precheck compare` as a subprocess (mirroring real usage). Exit code 0
means every executed check produced identical status+details in both modes.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GOOD_CALC = '''"""Tiny calculator module used by the demo checks."""


def add(a, b):
    return a + b
'''

BAD_STYLE = "x = 1\n" + "y = '" + "z" * 200 + "'\n"
BAD_SYNTAX = "def broken(:\n    pass\n"


class ConsistencyTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="precheck-consistency-")
        shutil.copytree(os.path.join(REPO_ROOT, "demo"), os.path.join(self.root, "demo"))
        shutil.copy2(os.path.join(REPO_ROOT, "precheck.json"),
                     os.path.join(self.root, "precheck.json"))

    def tearDown(self):
        shutil.rmtree(self.root, True)

    def write(self, rel, content):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def compare(self, *changed):
        env = dict(os.environ, PYTHONPATH=REPO_ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
        proc = subprocess.run(
            [sys.executable, "-m", "precheck.cli", "compare",
             "--root", self.root, "--changed", *changed],
            capture_output=True, text=True, cwd=REPO_ROOT, env=env,
        )
        return proc

    def assert_consistent(self, proc, expected_affected):
        self.assertEqual(
            proc.returncode, 0,
            msg=f"compare reported inconsistency:\n{proc.stdout}\n{proc.stderr}",
        )
        self.assertIn("result: CONSISTENT", proc.stdout)
        for name in expected_affected:
            line = next(l for l in proc.stdout.splitlines() if l.strip().startswith(f"{name}:"))
            self.assertIn("identical", line, msg=line)

    def test_single_src_file(self):
        self.write("demo/src/calculator.py", GOOD_CALC)
        self.assert_consistent(
            self.compare("demo/src/calculator.py"),
            ["py-syntax", "py-style", "py-tests"],
        )

    def test_single_test_file(self):
        self.write(
            "demo/tests/test_x.py",
            "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n",
        )
        self.assert_consistent(
            self.compare("demo/tests/test_x.py"),
            ["py-syntax", "py-tests"],
        )

    def test_single_json_file(self):
        self.write("demo/data/x.json", json.dumps([1, 2, 3]))
        self.assert_consistent(self.compare("demo/data/x.json"), ["json-validate"])

    def test_multi_file_change(self):
        self.write("demo/src/calculator.py", GOOD_CALC)
        self.write("demo/data/x.json", "[]\n")
        proc = self.compare("demo/src/calculator.py", "demo/data/x.json", "README.md")
        self.assert_consistent(
            proc,
            ["py-syntax", "py-style", "py-tests", "json-validate"],
        )

    def test_config_change_full_fallback_consistent(self):
        with open(os.path.join(self.root, "precheck.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg["ignore_patterns"].append("**.txt")
        self.write("precheck.json", json.dumps(cfg))
        proc = self.compare("precheck.json")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout)
        for name in ("py-syntax", "py-style", "py-tests", "json-validate", "config-validate"):
            self.assertIn(f"{name}: identical", proc.stdout)

    def test_unknown_file_full_fallback_consistent(self):
        self.write("demo/assets/logo.png", "\x89PNG\r\n")
        proc = self.compare("demo/assets/logo.png")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout)
        self.assertIn("UNKNOWN_IMPACT", proc.stdout)
        self.assertIn("result: CONSISTENT", proc.stdout)

    def test_docs_only_change_runs_nothing(self):
        proc = self.compare("README.md")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout)
        self.assertIn("affected checks: (none)", proc.stdout)

    def test_fault_style_is_reported_identically(self):
        self.write("demo/src/calculator.py", BAD_STYLE)
        proc = self.compare("demo/src/calculator.py")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout)
        self.assertIn("py-style: identical (FAIL)", proc.stdout)
        self.assertIn("verdict(affected): incremental=FAIL full=FAIL", proc.stdout)

    def test_fault_syntax_is_reported_identically(self):
        self.write("demo/src/strings.py", BAD_SYNTAX)
        proc = self.compare("demo/src/strings.py")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout)
        self.assertIn("py-syntax: identical (FAIL)", proc.stdout)

    def test_invalid_config_fallback_is_consistent(self):
        self.write("precheck.json", "{ broken json")
        proc = self.compare("demo/src/calculator.py")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout)
        self.assertIn("CONFIG_INVALID", proc.stdout)
        self.assertIn("config-validate: identical (FAIL)", proc.stdout)


if __name__ == "__main__":
    unittest.main()
