"""Core guarantee: incremental merged output == full output, same tree."""

import helpers
from helpers import C
from inccheck.model import Status


class TestEquivalence(helpers.RepoCase):
    def test_clean_tree_no_changes(self):
        self.baseline()
        result = self.assert_equivalent([])
        self.assertEqual(result.metrics["failed_checks"], 0)

    def test_single_file_change_introducing_failure(self):
        self.baseline()
        # pricing.py now imports a module that does not exist
        self.append("src/shop/pricing.py", "\nimport nonexistent_pkg\n")
        result = self.assert_equivalent([C("M", "src/shop/pricing.py")])
        self.assertEqual(result.metrics["failed_checks"], 1)
        self.assertLess(result.metrics["targets_ran"],
                        result.metrics["targets_total"])

    def test_multi_file_change(self):
        self.baseline()
        self.append("src/shop/inventory.py", "\nx = '" + "y" * 120 + "'\n")
        self.write("docs/README.md", "no heading here\n")
        result = self.assert_equivalent([C("M", "src/shop/inventory.py"),
                                         C("M", "docs/README.md")])
        self.assertEqual(result.metrics["failed_checks"], 2)

    def test_config_change_tightens_line_limit(self):
        self.baseline()
        self.append("src/shop/inventory.py", "\nz = '" + "q" * 90 + "'\n")
        self.write("checks.conf", "[checks]\nline-length = 80\n")
        result = self.assert_equivalent([C("M", "checks.conf"),
                                         C("M", "src/shop/inventory.py")])
        # 90-char line passes at 100, fails at 80: config change must re-run
        self.assertEqual(result.metrics["failed_checks"], 1)

    def test_uninferable_change_falls_back_and_matches(self):
        self.baseline()
        self.write("data/notes.txt", "random\n")
        result = self.assert_equivalent([C("A", "data/notes.txt")])
        self.assertEqual(result.plan.mode, "full")

    def test_new_module_listed_in_manifest(self):
        self.baseline()
        self.write("src/shop/search.py",
                   '"""Search."""\n\n\ndef find(q):\n    return []\n')
        self.append("MANIFEST.txt", "shop.search\n")
        result = self.assert_equivalent([C("A", "src/shop/search.py"),
                                         C("M", "MANIFEST.txt")])
        self.assertEqual(result.metrics["failed_checks"], 0)

    def test_new_module_missing_from_manifest_fails_c004(self):
        self.baseline()
        self.write("src/shop/search.py",
                   '"""Search."""\n\n\ndef find(q):\n    return []\n')
        result = self.assert_equivalent([C("A", "src/shop/search.py")])
        self.assertEqual(result.metrics["failed_checks"], 1)
        kinds = {(f.check_id, f.status) for f in result.findings}
        self.assertIn(("C004", Status.FAIL), kinds)

    def test_deleted_module_breaks_importers(self):
        self.baseline()
        self.delete("src/shop/inventory.py")
        # manifest still lists shop.inventory -> C004 fails too
        result = self.assert_equivalent([C("D", "src/shop/inventory.py")])
        failed = {f.check_id for f in result.findings
                  if f.status == Status.FAIL}
        self.assertEqual(failed, {"C002", "C004"})

    def test_syntax_error_falls_back_and_still_matches(self):
        self.baseline()
        self.write("src/shop/billing.py", "def broken(:\n")
        result = self.assert_equivalent([C("M", "src/shop/billing.py")])
        self.assertEqual(result.plan.mode, "full")
        failed = {f.check_id for f in result.findings
                  if f.status == Status.FAIL}
        self.assertEqual(failed, {"C001"})

    def test_second_incremental_reuses_first_cache(self):
        """Chained increments: cache written by run N feeds run N+1."""
        self.baseline()
        self.append("src/shop/pricing.py", "\nimport nonexistent_pkg\n")
        self.assert_equivalent([C("M", "src/shop/pricing.py")])
        self.append("src/shop/inventory.py", "\nEXTRA = 1\n")
        result = self.assert_equivalent([C("M", "src/shop/inventory.py")])
        # the pricing failure persists and must still appear in the report
        failed = [f for f in result.findings if f.status == Status.FAIL]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].target, "src/shop/pricing.py")
