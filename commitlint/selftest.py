#!/usr/bin/env python3
"""commitlint 自测：python3 selftest.py [-v]"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import commitlint as cl

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "rules.json")
DATASET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "labeled_commits.json")


def rules():
    return cl.load_rules(RULES_PATH)


def make_commit(**kwargs):
    defaults = dict(message="feat(api): add thing",
                    files=["src/api/thing.py"],
                    author_name="Dev", author_email="dev@example.com")
    defaults.update(kwargs)
    return cl.Commit(**defaults)


class TestStructure(unittest.TestCase):
    def test_valid_commit_passes(self):
        result = cl.check_commit(make_commit(), rules())
        self.assertEqual(result.status, "pass")

    def test_bad_format(self):
        result = cl.check_commit(make_commit(message="updated stuff"), rules())
        self.assertEqual(result.status, "fail")
        self.assertIn("bad-format", [f.code for f in result.findings])

    def test_unknown_type(self):
        result = cl.check_commit(
            make_commit(message="wip(api): add thing"), rules())
        self.assertIn("bad-type", [f.code for f in result.findings])

    def test_unknown_scope(self):
        result = cl.check_commit(
            make_commit(message="feat(billing): add thing"), rules())
        self.assertIn("unknown-scope", [f.code for f in result.findings])

    def test_empty_message(self):
        result = cl.check_commit(make_commit(message=""), rules())
        self.assertEqual(result.status, "fail")
        self.assertIn("bad-format", [f.code for f in result.findings])


class TestDescription(unittest.TestCase):
    def test_overlong_description(self):
        result = cl.check_commit(
            make_commit(message="feat(api): " + "x" * 73), rules())
        self.assertIn("description-too-long", [f.code for f in result.findings])

    def test_exactly_max_length_passes(self):
        result = cl.check_commit(
            make_commit(message="feat(api): " + "x" * 72), rules())
        self.assertNotIn("description-too-long",
                         [f.code for f in result.findings])

    def test_non_ascii_description(self):
        result = cl.check_commit(
            make_commit(message="fix(core): 修复缓存问题",
                        files=["src/core/cache.py"]), rules())
        self.assertIn("non-ascii-description",
                      [f.code for f in result.findings])

    def test_empty_description(self):
        result = cl.check_commit(make_commit(message="feat(api): "), rules())
        self.assertEqual(result.status, "fail")


class TestScopeConsistency(unittest.TestCase):
    def test_file_out_of_scope(self):
        result = cl.check_commit(
            make_commit(files=["src/api/a.py", "src/ui/b.tsx"]), rules())
        self.assertIn("file-out-of-scope", [f.code for f in result.findings])

    def test_shared_files_allowed(self):
        result = cl.check_commit(
            make_commit(files=["src/api/a.py", "README.md"]), rules())
        self.assertEqual(result.status, "pass")

    def test_no_scope_skips_consistency(self):
        result = cl.check_commit(
            make_commit(message="docs: update guide",
                        files=["docs/guide.md"]), rules())
        self.assertEqual(result.status, "pass")


class TestEmptyCommit(unittest.TestCase):
    def test_empty_commit_fails(self):
        result = cl.check_commit(make_commit(files=[]), rules())
        self.assertEqual(result.status, "fail")
        self.assertIn("empty-commit", [f.code for f in result.findings])

    def test_empty_commit_allowed_when_configured(self):
        relaxed = rules()
        relaxed["require_changes"] = False
        result = cl.check_commit(make_commit(files=[]), relaxed)
        self.assertEqual(result.status, "pass")


class TestExemptions(unittest.TestCase):
    def test_merge_exempt_and_auditable(self):
        result = cl.check_commit(
            make_commit(message="Merge branch 'x' into main", files=[]),
            rules())
        self.assertEqual(result.status, "exempt")
        self.assertEqual(result.exemption["rule"], "merge")
        self.assertTrue(result.exemption["reason"])

    def test_revert_exempt(self):
        msg = 'Revert "feat(api): add thing"\n\nThis reverts commit abc123.\n'
        result = cl.check_commit(make_commit(message=msg), rules())
        self.assertEqual(result.status, "exempt")
        self.assertEqual(result.exemption["rule"], "revert")

    def test_revert_without_body_not_exempt(self):
        result = cl.check_commit(
            make_commit(message='Revert "feat(api): add thing"'), rules())
        self.assertNotEqual(result.status, "exempt")

    def test_bot_exempt_by_email(self):
        result = cl.check_commit(
            make_commit(author_name="dependabot[bot]",
                        author_email="dependabot[bot]@users.noreply.github.com"),
            rules())
        self.assertEqual(result.status, "exempt")
        self.assertEqual(result.exemption["rule"], "bot")

    def test_bot_pattern_does_not_catch_humans(self):
        # 回归：fnmatch 的 [bot] 是字符类，必须转义，否则 bob/Carol 都被豁免
        for name, email in [("Bob", "bob@example.com"),
                            ("Carol", "carol@example.com"),
                            ("robot-lily", "lily@example.com")]:
            result = cl.check_commit(
                make_commit(author_name=name, author_email=email), rules())
            self.assertEqual(result.status, "pass",
                             "%s should not be exempt" % name)

    def test_disabled_exemption_not_applied(self):
        strict = rules()
        strict["exemptions"]["merge"]["enabled"] = False
        result = cl.check_commit(
            make_commit(message="Merge branch 'x'", files=[]), strict)
        self.assertEqual(result.status, "fail")


class TestCli(unittest.TestCase):
    def run_cli(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cl.main(argv)
        return code, buf.getvalue()

    def test_check_exit_codes(self):
        code, _ = self.run_cli([
            "--rules", RULES_PATH, "check",
            "--message", "feat(api): add thing",
            "--files", "src/api/thing.py"])
        self.assertEqual(code, cl.EXIT_OK)

        code, out = self.run_cli([
            "--rules", RULES_PATH, "check",
            "--message", "feat(api): " + "x" * 80,
            "--files", "src/api/thing.py"])
        self.assertEqual(code, cl.EXIT_VIOLATION)
        self.assertIn("description-too-long", out)

    def test_evaluate_dataset_fully_consistent(self):
        code, out = self.run_cli([
            "--rules", RULES_PATH, "evaluate", "--dataset", DATASET_PATH])
        self.assertEqual(code, cl.EXIT_OK, out)
        self.assertIn("0 mismatch", out)

    def test_evaluate_reports_fp_and_fn(self):
        dataset = {"commits": [
            {"id": "fp1", "message": "bad message", "files": ["x.py"],
             "human_label": "pass"},   # 脚本判违规 -> 误报
            {"id": "fn1", "message": "feat(api): ok", "files": ["src/api/a.py"],
             "human_label": "fail"},   # 脚本判通过 -> 漏报
        ]}
        with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(dataset, fh)
            path = fh.name
        try:
            code, out = self.run_cli([
                "--rules", RULES_PATH, "evaluate", "--dataset", path])
        finally:
            os.unlink(path)
        self.assertEqual(code, cl.EXIT_VIOLATION)
        self.assertIn("误报 false-positive (脚本违规/人工通过): 1", out)
        self.assertIn("漏报 false-negative (脚本通过/人工违规): 1", out)
        self.assertIn("2 mismatch", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
