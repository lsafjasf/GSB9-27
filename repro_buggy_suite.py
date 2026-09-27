"""复现套件：每个用例单独跑都绿，全量/乱序跑就随机失败。

注意：文件名故意不以 test_ 开头，避免被 unittest discover 误收。
"""
import os
import unittest

from fixture_buggy import SharedFixture, REGISTRY, COUNTER, TMPDIR, ENV_KEY


class OrderDependentSuite(unittest.TestCase):
    def setUp(self):
        self.fx = SharedFixture()
        self.fx.setup()

    def tearDown(self):
        self.fx.teardown()

    def test_01_counter_starts_at_one(self):
        # 只有第一个执行的用例能过：COUNTER 被每个用例的 setup 自增且永不复位
        self.assertEqual(COUNTER["n"], 1)

    def test_02_registry_has_only_baseline(self):
        # 任何先跑的用例只要写过 REGISTRY，这里就炸
        self.assertEqual(set(REGISTRY), {"initialized"})

    def test_03_tmpdir_is_empty(self):
        # 任何先跑的用例只要往共享 TMPDIR 写过文件，这里就炸
        self.assertEqual(os.listdir(TMPDIR), [])

    def test_04_env_not_leaked(self):
        # test_05 若先跑，会把 ENV_KEY 泄漏进进程环境
        self.assertNotIn(ENV_KEY, os.environ)

    def test_05_configure_env(self):
        # 写入方：直接污染进程环境，且不清理
        os.environ[ENV_KEY] = "testing"
        self.assertEqual(os.environ[ENV_KEY], "testing")

    def test_06_write_work_file(self):
        # 写入方：往共享 TMPDIR 丢文件
        with open(os.path.join(TMPDIR, "work.txt"), "w") as f:
            f.write("x")
        self.assertIn("work.txt", os.listdir(TMPDIR))

    def test_07_register_plugin(self):
        # 写入方：往全局注册表塞键
        REGISTRY["plugin_alpha"] = object()
        self.assertIn("plugin_alpha", REGISTRY)

    def test_08_boom_leaves_mess(self):
        # 失败路径污染：抛异常前已写入全局状态，teardown 不清理
        REGISTRY["boom"] = True
        with open(os.path.join(TMPDIR, "boom.tmp"), "w") as f:
            f.write("boom")
        raise RuntimeError("boom: 用例失败，污染已残留")
