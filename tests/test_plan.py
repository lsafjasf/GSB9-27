"""Scenario coverage: single file, multi file, config, uninferable, edge cases."""

import helpers
from helpers import C
from inccheck import engine


def slots(plan):
    return {(t.check_id, t.path) for t in plan.targets}


class TestSingleFile(helpers.RepoCase):
    def test_leaf_module_fans_out_to_importers(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("M", "src/shop/pricing.py")])
        self.assertEqual(pl.mode, "incremental")
        self.assertEqual(slots(pl), {
            ("C001", "src/shop/pricing.py"),
            ("C002", "src/shop/pricing.py"),
            ("C003", "src/shop/pricing.py"),
            ("C002", "src/shop/cart.py"),       # direct importer
            ("C002", "src/shop/billing.py"),    # direct importer
            ("C002", "src/shop/checkout.py"),   # transitive importer
        })

    def test_unimported_module_touches_only_itself(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("M", "src/shop/checkout.py")])
        self.assertEqual(slots(pl), {
            ("C001", "src/shop/checkout.py"),
            ("C002", "src/shop/checkout.py"),
            ("C003", "src/shop/checkout.py"),
        })

    def test_doc_file_maps_to_doc_check_only(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("M", "docs/README.md")])
        self.assertEqual(slots(pl), {("C005", "docs/README.md")})


class TestMultiFile(helpers.RepoCase):
    def test_union_of_changes(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("M", "src/shop/inventory.py"),
                                     C("M", "docs/README.md")])
        self.assertEqual(slots(pl), {
            ("C001", "src/shop/inventory.py"),
            ("C002", "src/shop/inventory.py"),
            ("C003", "src/shop/inventory.py"),
            ("C002", "src/shop/cart.py"),       # imports inventory
            ("C002", "src/shop/checkout.py"),   # imports cart
            ("C005", "docs/README.md"),
        })

    def test_overlapping_changes_deduplicate(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("M", "src/shop/pricing.py"),
                                     C("M", "src/shop/cart.py")])
        # own checks (6) + C002 on billing and checkout; dedup via set
        self.assertEqual(len(pl.targets), 8)
        self.assertIn(("C002", "src/shop/checkout.py"), slots(pl))


class TestConfigChange(helpers.RepoCase):
    def test_checks_conf_reruns_all_checks(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("M", "checks.conf")])
        self.assertEqual(pl.mode, "incremental")
        self.assertEqual(len(pl.targets), 21)  # every target in the repo

    def test_manifest_change_reruns_only_c004(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("M", "MANIFEST.txt")])
        self.assertEqual(slots(pl), {("C004", None)})


class TestUninferable(helpers.RepoCase):
    def test_unknown_extension_falls_back(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("A", "data/notes.txt")])
        self.assertEqual(pl.mode, "full")
        self.assertTrue(any("F2" in r for r in pl.fallback_reasons))

    def test_unknown_nested_config_falls_back(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("A", "src/shop/settings.ini")])
        self.assertEqual(pl.mode, "full")

    def test_cold_cache_falls_back(self):
        pl = engine.plan(self.repo, [C("M", "src/shop/pricing.py")],
                         cache_exists=False)
        self.assertEqual(pl.mode, "full")
        self.assertTrue(any("F1" in r for r in pl.fallback_reasons))

    def test_unparseable_tree_falls_back(self):
        self.baseline()
        self.write("src/shop/billing.py", "def broken(:\n")
        pl = engine.plan(self.repo, [C("M", "src/shop/billing.py")])
        self.assertEqual(pl.mode, "full")
        self.assertTrue(any("F3" in r for r in pl.fallback_reasons))

    def test_path_escape_falls_back(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("M", "../outside.py")])
        self.assertEqual(pl.mode, "full")
        self.assertTrue(any("F4" in r for r in pl.fallback_reasons))

    def test_bad_status_falls_back(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("X", "src/shop/pricing.py")])
        self.assertEqual(pl.mode, "full")
        self.assertTrue(any("F5" in r for r in pl.fallback_reasons))


class TestAddDelete(helpers.RepoCase):
    def test_new_module_runs_own_checks_and_manifest(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("A", "src/shop/search.py")])
        self.assertEqual(slots(pl), {
            ("C001", "src/shop/search.py"),
            ("C002", "src/shop/search.py"),
            ("C003", "src/shop/search.py"),
            ("C004", None),
        })

    def test_deleted_module_fans_out_to_importers(self):
        self.baseline()
        pl = engine.plan(self.repo, [C("D", "src/shop/inventory.py")])
        self.assertEqual(slots(pl), {
            ("C002", "src/shop/cart.py"),
            ("C002", "src/shop/checkout.py"),
            ("C004", None),
        })
