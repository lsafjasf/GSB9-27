"""CLI contract: exit codes, deterministic report, metrics channel."""

import io
import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout

import helpers
from inccheck.cli import main


class TestCli(helpers.RepoCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_full_exit_zero_on_clean_repo(self):
        code, out, _ = self.run_cli("full", "--root", self.root)
        self.assertEqual(code, 0)
        self.assertIn("SUMMARY total=21 failed=0", out)

    def test_inc_exit_one_on_failure(self):
        self.run_cli("full", "--root", self.root)
        self.append("src/shop/pricing.py", "\nimport ghost_mod\n")
        code, out, err = self.run_cli(
            "inc", "--root", self.root, "--changed", "M:src/shop/pricing.py")
        self.assertEqual(code, 1)
        self.assertIn("C002 src/shop/pricing.py FAIL", out)
        self.assertIn("mode=incremental", err)

    def test_report_is_deterministic(self):
        self.run_cli("full", "--root", self.root)
        _, first, _ = self.run_cli(
            "inc", "--root", self.root, "--changed", "M:src/shop/pricing.py")
        _, second, _ = self.run_cli(
            "inc", "--root", self.root, "--changed", "M:src/shop/pricing.py")
        self.assertEqual(first, second)

    def test_metrics_json(self):
        self.run_cli("full", "--root", self.root)
        with tempfile.NamedTemporaryFile("r", suffix=".json",
                                         delete=False) as fh:
            path = fh.name
        try:
            self.run_cli("inc", "--root", self.root, "--metrics-json", path,
                         "--changed", "M:src/shop/pricing.py")
            with open(path, encoding="utf-8") as fh:
                metrics = json.load(fh)
            self.assertEqual(metrics["mode"], "incremental")
            self.assertEqual(metrics["targets_total"], 21)
        finally:
            os.unlink(path)

    def test_plan_command(self):
        self.run_cli("full", "--root", self.root)
        code, out, _ = self.run_cli(
            "plan", "--root", self.root, "--changed", "M:src/shop/pricing.py")
        self.assertEqual(code, 0)
        self.assertIn("mode=incremental", out)
        self.assertIn("C002 src/shop/checkout.py", out)  # transitive fan-out
