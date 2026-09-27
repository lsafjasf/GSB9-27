#!/usr/bin/env python3
"""secure_sandbox 回归测试。

运行：python3 -m unittest test_secure_sandbox -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import secure_sandbox
from secure_sandbox import (
    InvalidPathError,
    NotARegularFileError,
    PathEscapeError,
    Sandbox,
)

SECRET = "TOP-SECRET-OUTSIDE-CONTENT"


class SandboxTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="sandbox-test-")
        outside = os.path.join(cls.base, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "secret.txt"), "w") as f:
            f.write(SECRET)
        cls.root = os.path.join(cls.base, "sandbox")
        os.makedirs(os.path.join(cls.root, "sub"))
        with open(os.path.join(cls.root, "ok.txt"), "w") as f:
            f.write("hello inside")
        with open(os.path.join(cls.root, "sub", "note.txt"), "w") as f:
            f.write("note")
        os.symlink(os.path.join("..", "outside", "secret.txt"),
                   os.path.join(cls.root, "link_out"))
        os.symlink("link_b", os.path.join(cls.root, "link_a"))
        os.symlink(os.path.join("..", "outside", "secret.txt"),
                   os.path.join(cls.root, "link_b"))
        os.symlink("note.txt", os.path.join(cls.root, "sub", "link_in"))
        cls.sandbox = Sandbox(cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def read(self, user_path: str) -> str:
        with self.sandbox.open(user_path) as f:
            return f.read().decode()


class TestLegitAccess(SandboxTestBase):
    def test_root_file(self):
        self.assertEqual(self.read("ok.txt"), "hello inside")

    def test_subdir_file(self):
        self.assertEqual(self.read("sub/note.txt"), "note")

    def test_dot_segments_inside(self):
        self.assertEqual(self.read("sub/../ok.txt"), "hello inside")
        self.assertEqual(self.read("./ok.txt"), "hello inside")

    def test_symlink_pointing_inside_allowed(self):
        self.assertEqual(self.read("sub/link_in"), "note")

    def test_missing_file_inside_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError):
            self.sandbox.open("no-such-file.txt")


class TestRelativePathEscape(SandboxTestBase):
    def test_single_level(self):
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("../outside/secret.txt")

    def test_multi_level(self):
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("sub/../../../outside/secret.txt")

    def test_dot_dot_slash_mix(self):
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("./../../outside/secret.txt")

    def test_prefix_sibling_dir(self):
        """/tmp/x/sandbox_evil 不能通过 /tmp/x/sandbox 的边界。"""
        evil = os.path.join(self.base, "sandbox_evil")
        os.makedirs(evil, exist_ok=True)
        with open(os.path.join(evil, "evil.txt"), "w") as f:
            f.write("EVIL")
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("../sandbox_evil/evil.txt")

    def test_missing_file_outside_still_rejected(self):
        """沙箱外路径即使不存在也必须拒绝（不泄露拓扑）。"""
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("../outside/missing.txt")


class TestEncodedSeparators(SandboxTestBase):
    def test_encoded_dotdot_slash(self):
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("%2e%2e%2foutside%2fsecret.txt")

    def test_encoded_slash_only(self):
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("..%2foutside%2fsecret.txt")

    def test_mixed_encoding(self):
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("%2e%2e/outside/secret.txt")

    def test_double_encoding_stays_literal(self):
        """%252e 只解码一次，得到字面量 '%2e' 文件名，不构成逃逸。"""
        with self.assertRaises(FileNotFoundError):
            self.sandbox.open("%252e%252e/outside/secret.txt")

    def test_invalid_percent_sequence(self):
        with self.assertRaises((InvalidPathError, FileNotFoundError)):
            self.sandbox.open("%zz%gg/secret.txt")


class TestSymlinks(SandboxTestBase):
    def test_symlink_to_outside(self):
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("link_out")

    def test_symlink_chain(self):
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("link_a")

    def test_relative_plus_symlink(self):
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("sub/../link_out")

    def test_symlink_in_subdir_component(self):
        os.symlink(os.path.join("..", "..", "outside"),
                   os.path.join(self.root, "sub", "dirlink"))
        self.addCleanup(os.unlink, os.path.join(self.root, "sub", "dirlink"))
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("sub/dirlink/secret.txt")


class TestCaseDifference(SandboxTestBase):
    def test_case_variant_outside_rejected(self):
        """大小写变体解析到沙箱外时必须拒绝。

        Linux/ext4 上大小写敏感，../OUTSIDE 不存在，但 realpath 词法解析后
        仍落在沙箱外，必须在打开前就被边界判定拒绝。
        """
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("../OUTSIDE/secret.txt")
        with self.assertRaises(PathEscapeError):
            self.sandbox.open("../Outside/Secret.TXT")


class TestMalformedInput(SandboxTestBase):
    def test_absolute_path(self):
        with self.assertRaises(InvalidPathError):
            self.sandbox.open(os.path.join(self.base, "outside", "secret.txt"))

    def test_nul_byte(self):
        with self.assertRaises(InvalidPathError):
            self.sandbox.open("ok.txt\x00.txt")

    def test_empty_path(self):
        with self.assertRaises(InvalidPathError):
            self.sandbox.open("")

    def test_component_too_long(self):
        with self.assertRaises(InvalidPathError):
            self.sandbox.open("a" * 300 + ".txt")

    def test_total_path_too_long(self):
        with self.assertRaises(InvalidPathError):
            self.sandbox.open("/".join(["d"] * 3000))


class TestDirectories(SandboxTestBase):
    def test_root_itself(self):
        with self.assertRaises(NotARegularFileError):
            self.sandbox.open(".")

    def test_subdir_itself(self):
        with self.assertRaises(NotARegularFileError):
            self.sandbox.open("sub")

    def test_resolves_back_to_root(self):
        with self.assertRaises(NotARegularFileError):
            self.sandbox.open("sub/..")


class TestRaceMitigation(SandboxTestBase):
    def test_dir_swapped_for_symlink_after_resolve(self):
        """模拟 TOCTOU：resolve 之后中间目录被换成指向外部的符号链接。

        _open_beneath 逐分量 O_NOFOLLOW 遍历，遇到被替换的符号链接必须
        失败（ELOOP），而不是顺着它读到沙箱外。
        """
        swap = os.path.join(self.root, "swap")
        os.makedirs(swap)
        with open(os.path.join(swap, "f.txt"), "w") as f:
            f.write("x")
        real = self.sandbox.resolve("swap/f.txt")  # 合法解析
        # 模拟竞态替换：删掉目录，换成指向外部的符号链接
        os.unlink(os.path.join(swap, "f.txt"))
        os.rmdir(swap)
        os.symlink(os.path.join("..", "outside"), swap)
        self.addCleanup(os.unlink, swap)
        with self.assertRaises((PathEscapeError, OSError)):
            self.sandbox._open_beneath(real)

    def test_final_component_swapped_for_symlink(self):
        target = os.path.join(self.root, "race.txt")
        with open(target, "w") as f:
            f.write("x")
        real = self.sandbox.resolve("race.txt")
        os.unlink(target)
        os.symlink(os.path.join("..", "outside", "secret.txt"), target)
        self.addCleanup(os.unlink, target)
        with self.assertRaises((PathEscapeError, OSError)):
            self.sandbox._open_beneath(real)


class TestCrossMount(SandboxTestBase):
    def test_tmpfs_mounted_inside_sandbox_rejected(self):
        """沙箱内的挂载点（tmpfs）通向沙箱外数据，必须被 st_dev 检查拒绝。"""
        if shutil.which("unshare") is None:
            self.skipTest("unshare 不可用")
        helper = r'''
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "src"))
import secure_sandbox
root = sys.argv[2]
os.system("mount -t tmpfs tmpfs " + os.path.join(root, "mnt"))
with open(os.path.join(root, "mnt", "secret.txt"), "w") as f:
    f.write("MOUNTED-SECRET")
sb = secure_sandbox.Sandbox(root)
try:
    sb.open("mnt/secret.txt")
except secure_sandbox.PathEscapeError:
    sys.exit(0)
sys.exit(1)
'''
        mnt = os.path.join(self.root, "mnt")
        os.makedirs(mnt, exist_ok=True)
        here = os.path.dirname(os.path.abspath(__file__))
        proc = subprocess.run(
            ["unshare", "-rm", sys.executable, "-c", helper, here, self.root],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode not in (0, 1):
            self.skipTest(f"unshare 命名空间不可用: {proc.stderr.strip()[:100]}")
        self.assertEqual(proc.returncode, 0,
                         f"跨挂载点未被拒绝: {proc.stdout} {proc.stderr}")

    def test_allow_mounts_opt_in(self):
        """显式 allow_mounts=True 时不做 st_dev 限制（仅构造验证）。"""
        sb = Sandbox(self.root, allow_mounts=True)
        self.assertTrue(sb.allow_mounts)


if __name__ == "__main__":
    unittest.main()
