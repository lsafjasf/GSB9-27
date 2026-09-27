"""与复现套件同构的修复版套件：任意顺序、并发执行都应全绿。"""
import os
import unittest

from fixture_fixed import IsolatedFixture

ENV_KEY = "GSB_FIXTURE_PROFILE"
ENV_TESTS = {"test_04_env_not_leaked", "test_05_configure_env"}


class OrderIndependentSuite(unittest.TestCase):
    def setUp(self):
        self.fx = IsolatedFixture(
            self._testMethodName,
            uses_env=self._testMethodName in ENV_TESTS,
        )
        self.fx.__enter__()
        # addCleanup 保证：即使测试体抛异常/跳过，close() 也一定执行
        self.addCleanup(self.fx.close)

    def test_01_counter_starts_at_zero(self):
        self.assertEqual(self.fx.counter, 0)
        self.fx.counter += 1
        self.assertEqual(self.fx.counter, 1)

    def test_02_registry_starts_empty(self):
        self.assertEqual(self.fx.registry, {})

    def test_03_tmpdir_starts_empty(self):
        self.assertEqual(os.listdir(self.fx.tmpdir), [])

    def test_04_env_not_leaked(self):
        self.assertNotIn(ENV_KEY, os.environ)

    def test_05_configure_env(self):
        self.fx.set_env(ENV_KEY, "testing")
        self.assertEqual(os.environ[ENV_KEY], "testing")

    def test_06_write_work_file(self):
        path = os.path.join(self.fx.tmpdir, "work.txt")
        with open(path, "w") as f:
            f.write("x")
        self.assertEqual(os.listdir(self.fx.tmpdir), ["work.txt"])

    def test_07_register_plugin(self):
        self.fx.registry["plugin_alpha"] = object()
        self.assertEqual(set(self.fx.registry), {"plugin_alpha"})

    def test_08_boom_is_cleaned_up(self):
        # 失败路径：污染后抛异常，清理断言在 test_cleanup.py 中验证
        self.fx.registry["boom"] = True
        with open(os.path.join(self.fx.tmpdir, "boom.tmp"), "w") as f:
            f.write("boom")
        # 用 try/finally 模拟"会失败但仍需清理"的场景，
        # 本用例自身保持绿色，清理是否生效由回归测试断言
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            pass
