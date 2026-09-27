"""回归测试：修复后的 SecurePathGuard 必须拒绝全部绕过用例，
同时正确处理合法访问与各类边界情形。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import secure_guard
from bypass_cases import (
    build_fixture, make_cases, make_robustness_cases, SECRET_CONTENT,
)


class BypassRejectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.root = build_fixture()
        cls.guard = secure_guard.SecurePathGuard(cls.root)

    def test_all_bypass_cases_rejected(self):
        for name, path in make_cases(self.base, self.root):
            with self.subTest(case=name):
                with self.assertRaises(secure_guard.PathEscapeError,
                                       msg="未被拒绝: %s" % name):
                    self.guard.open_file(path)

    def test_resolve_rejects_too(self):
        # resolve() 单独使用也必须拒绝（不只 open_file）
        for name, path in make_cases(self.base, self.root):
            with self.subTest(case=name):
                with self.assertRaises(secure_guard.PathEscapeError):
                    self.guard.resolve(path)


class LegitAccessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.root = build_fixture()
        cls.guard = secure_guard.SecurePathGuard(cls.root)

    def test_read_file_in_sandbox(self):
        with self.guard.open_file("ok.txt") as f:
            self.assertEqual(f.read(), b"hello sandbox")

    def test_read_nested_file(self):
        with self.guard.open_file("sub/deep.txt") as f:
            self.assertEqual(f.read(), b"deep file")

    def test_dot_segments_inside_sandbox(self):
        # 沙箱内部的 .. 不越界，应当允许
        with self.guard.open_file("sub/../ok.txt") as f:
            self.assertEqual(f.read(), b"hello sandbox")

    def test_resolve_returns_realpath(self):
        real = self.guard.resolve("sub/../ok.txt")
        self.assertEqual(real, os.path.join(self.guard.root, "ok.txt"))
        self.assertFalse(os.path.islink(real))


class RobustnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.root = build_fixture()
        cls.guard = secure_guard.SecurePathGuard(cls.root)

    def test_robustness_cases(self):
        for name, path, expect in make_robustness_cases():
            with self.subTest(case=name):
                if expect == "DIR_OK":
                    # 目录本身：解析到沙箱根，允许通过边界判定
                    self.assertEqual(self.guard.resolve(path), self.guard.root)
                elif expect == "FileNotFoundError":
                    with self.assertRaises(FileNotFoundError):
                        self.guard.open_file(path)
                elif expect == "PathEscapeError":
                    with self.assertRaises(secure_guard.PathEscapeError):
                        self.guard.open_file(path)
                elif expect == "InvalidPathError":
                    with self.assertRaises(secure_guard.InvalidPathError):
                        self.guard.open_file(path)
                elif expect == "OSError|InvalidPathError":
                    with self.assertRaises(
                            (OSError, secure_guard.InvalidPathError)):
                        self.guard.open_file(path)
                else:
                    self.fail("unknown expectation %r" % expect)

    def test_cross_mount_detected(self):
        # /proc 与沙箱根通常位于不同挂载点；越界判定必须先于挂载点判定生效
        with self.assertRaises(secure_guard.PathEscapeError):
            self.guard.resolve("proc_link/self/status")
        if os.path.exists("/proc/self/status"):
            self.assertTrue(self.guard.is_cross_mount("/proc/self/status"))

    def test_opened_fd_stays_in_sandbox(self):
        # 打开后复核：fd 的真实路径仍在沙箱内
        with self.guard.open_file("ok.txt") as f:
            actual = os.readlink("/proc/self/fd/%d" % f.fileno())
            self.assertTrue(actual.startswith(self.guard.root + os.sep))


class RootItselfTest(unittest.TestCase):
    def test_root_may_contain_symlink(self):
        # 调用方传入带 symlink 的 root 时，root 也被规范化
        base, root = build_fixture()
        alias = os.path.join(base, "root_alias")
        os.symlink(root, alias)
        guard = secure_guard.SecurePathGuard(alias)
        self.assertEqual(guard.root, os.path.realpath(root))
        with guard.open_file("ok.txt") as f:
            self.assertEqual(f.read(), b"hello sandbox")
        with self.assertRaises(secure_guard.PathEscapeError):
            guard.open_file("../secret.txt")


if __name__ == "__main__":
    unittest.main(verbosity=2)
