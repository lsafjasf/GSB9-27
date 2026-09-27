"""复现用例：每个用例单独跑都绿，全量/乱序跑就随机失败。"""
import os
import unittest

import fixture_buggy as fx


class TestSharedFixture(unittest.TestCase):
    def test_cache_returns_fresh_value(self):
        self.assertEqual(fx.cached_compute("answer", lambda: 42), 42)

    def test_cache_returns_other_value(self):
        self.assertEqual(fx.cached_compute("answer", lambda: 7), 7)

    def test_counter_equals_one(self):
        fx.bump()
        self.assertEqual(fx.COUNTER["value"], 1)

    def test_counter_equals_three(self):
        fx.bump(3)
        self.assertEqual(fx.COUNTER["value"], 3)

    def test_mode_defaults_to_none(self):
        self.assertIsNone(fx.get_mode())

    def test_mode_can_be_set(self):
        fx.set_mode("debug")
        self.assertEqual(fx.get_mode(), "debug")

    def test_registry_contains_only_alpha(self):
        fx.register("alpha", "A")
        self.assertEqual(fx.REGISTRY, {"alpha": "A"})

    def test_registry_contains_only_beta(self):
        fx.register("beta", "B")
        self.assertEqual(fx.REGISTRY, {"beta": "B"})

    def test_tmpdir_is_empty_then_writes_log(self):
        self.assertEqual(os.listdir(fx.workspace()), [])
        open(os.path.join(fx.workspace(), "app.log"), "w").close()

    def test_tmpdir_is_empty_then_writes_report(self):
        self.assertEqual(os.listdir(fx.workspace()), [])
        open(os.path.join(fx.workspace(), "report.txt"), "w").close()


if __name__ == "__main__":
    unittest.main()
