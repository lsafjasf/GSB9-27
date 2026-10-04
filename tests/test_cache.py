"""Cache key sensitivity: reuse only when nothing relevant changed."""

import helpers
from helpers import C
from inccheck import engine


class TestCacheKeys(helpers.RepoCase):
    def ran_slots(self, result):
        return {(t.check_id, t.path) for t in result.ran}

    def test_unrelated_change_reuses_everything_else(self):
        self.baseline()
        self.append("src/shop/inventory.py", "\nEXTRA = 1\n")
        result = engine.run_incremental(self.repo,
                                        [C("M", "src/shop/inventory.py")])
        # C001/C002/C003 on inventory itself + C002 on cart (its direct
        # dependency hash changed); checkout only depends on cart/billing.
        self.assertEqual(len(result.ran), 4)
        self.assertEqual(len(result.reused), 17)
        self.assertEqual(len(result.plan.targets), 5)  # static upper bound

    def test_config_change_invalidates_all_keys(self):
        self.baseline()
        self.write("checks.conf", "[checks]\nline-length = 88\n")
        result = engine.run_incremental(self.repo, [C("M", "checks.conf")])
        self.assertEqual(len(result.ran), 21)
        self.assertEqual(len(result.reused), 0)

    def test_same_content_change_is_reused(self):
        self.baseline()
        # touch with identical content: key unchanged -> cache hit
        content = self.read("src/shop/inventory.py")
        self.write("src/shop/inventory.py", content)
        result = engine.run_incremental(self.repo,
                                        [C("M", "src/shop/inventory.py")])
        self.assertEqual(len(result.ran), 0)
        self.assertEqual(len(result.reused), 21)

    def test_deleted_target_dropped_from_cache(self):
        self.baseline()
        self.delete("src/shop/inventory.py")
        engine.run_incremental(self.repo, [C("D", "src/shop/inventory.py")])
        entries = engine.Cache(self.repo).load()["entries"]
        self.assertNotIn("C001|src/shop/inventory.py", entries)
        self.assertEqual(len(entries), 18)

    def test_cold_start_then_warm(self):
        result = engine.run_incremental(self.repo,
                                        [C("M", "src/shop/pricing.py")])
        self.assertEqual(result.plan.mode, "full")  # F1 cold cache
        result2 = engine.run_incremental(self.repo,
                                         [C("M", "src/shop/pricing.py")])
        self.assertEqual(result2.plan.mode, "incremental")
