"""回归测试：与 tests_buggy 相同的 10 个用例改写为隔离夹具版，
另加失败/跳过路径的清理保证测试。"""
import os
import tempfile
import unittest

from fixture_fixed import Fixture, IsolatedTestCase


class TestIsolatedFixture(IsolatedTestCase):
    def test_cache_returns_fresh_value(self):
        self.assertEqual(self.fixture.cached_compute("answer", lambda: 42), 42)

    def test_cache_returns_other_value(self):
        self.assertEqual(self.fixture.cached_compute("answer", lambda: 7), 7)

    def test_counter_equals_one(self):
        self.fixture.bump()
        self.assertEqual(self.fixture.counter, 1)

    def test_counter_equals_three(self):
        self.fixture.bump(3)
        self.assertEqual(self.fixture.counter, 3)

    def test_mode_defaults_to_none(self):
        self.assertIsNone(self.fixture.getenv("APP_MODE"))

    def test_mode_can_be_set(self):
        self.fixture.set_env("APP_MODE", "debug")
        self.assertEqual(self.fixture.getenv("APP_MODE"), "debug")
        self.assertNotIn("APP_MODE", os.environ)  # 进程环境零污染

    def test_registry_contains_only_alpha(self):
        self.fixture.register("alpha", "A")
        self.assertEqual(self.fixture.registry, {"alpha": "A"})

    def test_registry_contains_only_beta(self):
        self.fixture.register("beta", "B")
        self.assertEqual(self.fixture.registry, {"beta": "B"})

    def test_tmpdir_is_empty_then_writes_log(self):
        self.assertEqual(os.listdir(self.fixture.workspace()), [])
        open(os.path.join(self.fixture.workspace(), "app.log"), "w").close()

    def test_tmpdir_is_empty_then_writes_report(self):
        self.assertEqual(os.listdir(self.fixture.workspace()), [])
        open(os.path.join(self.fixture.workspace(), "report.txt"), "w").close()


class TestCleanupGuarantees(unittest.TestCase):
    """清理必须在失败路径也生效。"""

    def test_cleanup_on_exception(self):
        fx = Fixture()
        with self.assertRaises(RuntimeError):
            with fx:
                tmp = fx.workspace()
                fx.register("k", "v")
                fx.set_env("APP_MODE", "debug")
                raise RuntimeError("boom")
        self.assertFalse(os.path.exists(tmp), "异常后临时目录必须被删除")

    def test_cleanup_on_skip(self):
        fx = Fixture()
        try:
            with fx:
                tmp = fx.workspace()
                raise unittest.SkipTest("skipped")
        except unittest.SkipTest:
            pass
        self.assertFalse(os.path.exists(tmp), "跳过后临时目录必须被删除")

    def test_close_is_idempotent(self):
        fx = Fixture()
        with fx:
            pass
        fx.close()  # 第二次调用不得抛错
        fx.close()

    def test_failing_and_skipped_cases_leave_no_trace(self):
        """用例抛异常或被跳过时，进程环境仍要还原。"""

        class _Failing(IsolatedTestCase):
            def runTest(self):
                self.fixture.set_env("APP_MODE", "debug")
                self.fixture.register("x", "1")
                open(os.path.join(self.fixture.workspace(), "f.txt"), "w").close()
                raise AssertionError("intentional")

        class _Skipped(IsolatedTestCase):
            def runTest(self):
                self.fixture.set_env("APP_MODE", "debug")
                raise unittest.SkipTest("intentional")

        env_before = dict(os.environ)
        tmp_before = set(os.listdir(tempfile.gettempdir()))

        result = unittest.TestResult()
        unittest.TestSuite([_Failing(), _Skipped()]).run(result)

        self.assertEqual(len(result.failures), 1)
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(dict(os.environ), env_before, "os.environ 必须原样还原")
        leftovers = {
            p for p in set(os.listdir(tempfile.gettempdir())) - tmp_before
            if p.startswith("gsb9_iso_")
        }
        self.assertEqual(leftovers, set(), f"临时目录泄漏: {leftovers}")


if __name__ == "__main__":
    unittest.main()
