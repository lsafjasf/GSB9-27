import unittest

from precheck.config import DEFAULT_CONFIG
from precheck.impact import infer


class ImpactTest(unittest.TestCase):
    def test_single_src_file_hits_three_checks(self):
        impact = infer(["demo/src/calculator.py"], DEFAULT_CONFIG)
        self.assertEqual(impact.mode, "subset")
        self.assertEqual(impact.affected, ["py-syntax", "py-style", "py-tests"])
        self.assertEqual(impact.fallback_reasons, [])

    def test_single_test_file_hits_two_checks(self):
        impact = infer(["demo/tests/test_calculator.py"], DEFAULT_CONFIG)
        self.assertEqual(impact.mode, "subset")
        self.assertEqual(impact.affected, ["py-syntax", "py-tests"])

    def test_single_json_file_hits_one_check(self):
        impact = infer(["demo/data/users.json"], DEFAULT_CONFIG)
        self.assertEqual(impact.mode, "subset")
        self.assertEqual(impact.affected, ["json-validate"])

    def test_multi_file_change_unions_affected_checks(self):
        impact = infer(
            ["demo/src/calculator.py", "demo/data/users.json", "README.md"],
            DEFAULT_CONFIG,
        )
        self.assertEqual(impact.mode, "subset")
        self.assertEqual(
            impact.affected,
            ["py-syntax", "py-style", "py-tests", "json-validate"],
        )

    def test_docs_only_change_needs_no_checks(self):
        impact = infer(["README.md", "demo/docs/guide.md"], DEFAULT_CONFIG)
        self.assertEqual(impact.mode, "none")
        self.assertEqual(impact.affected, [])

    def test_empty_change_set_needs_no_checks(self):
        impact = infer([], DEFAULT_CONFIG)
        self.assertEqual(impact.mode, "none")

    def test_config_change_forces_full_run(self):
        impact = infer(["precheck.json"], DEFAULT_CONFIG)
        self.assertEqual(impact.mode, "full")
        self.assertTrue(any("CONFIG_CHANGED" in r for r in impact.fallback_reasons))
        self.assertEqual(impact.affected, ["py-syntax", "py-style", "py-tests",
                                           "json-validate", "config-validate"])

    def test_dependency_map_doc_change_forces_full_run(self):
        impact = infer(["DEPENDENCY_MAP.md"], DEFAULT_CONFIG)
        self.assertEqual(impact.mode, "full")
        self.assertTrue(any("CONFIG_CHANGED" in r for r in impact.fallback_reasons))

    def test_unknown_file_forces_full_run(self):
        impact = infer(["demo/assets/logo.png"], DEFAULT_CONFIG)
        self.assertEqual(impact.mode, "full")
        self.assertTrue(any("UNKNOWN_IMPACT" in r for r in impact.fallback_reasons))

    def test_one_unknown_file_in_batch_forces_full_for_everything(self):
        impact = infer(["demo/src/calculator.py", "demo/src/unknown.bin"], DEFAULT_CONFIG)
        self.assertEqual(impact.mode, "full")

    def test_config_invalid_signal_forces_full_run(self):
        impact = infer([""], DEFAULT_CONFIG)
        self.assertEqual(impact.mode, "full")
        self.assertTrue(any("CONFIG_INVALID" in r for r in impact.fallback_reasons))

    def test_paths_are_normalized(self):
        impact = infer(["./demo/src/./calculator.py", "demo/src\\strings.py"], DEFAULT_CONFIG)
        self.assertEqual(impact.mode, "subset")
        self.assertEqual(impact.affected, ["py-syntax", "py-style", "py-tests"])


if __name__ == "__main__":
    unittest.main()
