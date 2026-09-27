#!/usr/bin/env python3
"""路径校验绕过用例集 + 检出证据。

对"有缺陷实现"与"修复后实现"分别运行同一批攻击用例：
  - 有缺陷实现：展示哪些用例能读到沙箱外文件（漏洞确认）。
  - 修复后实现：全部用例必须被拒绝（回归证据）。

用法：python3 bypass_cases.py
退出码：0 = 修复后实现拒绝了全部绕过用例；1 = 仍有用例得逞。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import secure_sandbox
import vulnerable_sandbox

SECRET = "TOP-SECRET-OUTSIDE-CONTENT"
EVIL = "EVIL-SIBLING-CONTENT"


def build_fixtures(base: str) -> dict:
    """构造攻击场景目录树，返回关键路径。"""
    outside = os.path.join(base, "outside")
    os.makedirs(outside)
    with open(os.path.join(outside, "secret.txt"), "w") as f:
        f.write(SECRET)

    evil = os.path.join(base, "sandbox_evil")  # 与沙箱根共享前缀的兄弟目录
    os.makedirs(evil)
    with open(os.path.join(evil, "evil.txt"), "w") as f:
        f.write(EVIL)

    root = os.path.join(base, "sandbox")
    os.makedirs(os.path.join(root, "sub"))
    with open(os.path.join(root, "ok.txt"), "w") as f:
        f.write("hello inside")
    with open(os.path.join(root, "sub", "note.txt"), "w") as f:
        f.write("note")
    os.symlink(os.path.join("..", "outside", "secret.txt"),
               os.path.join(root, "link_out"))
    os.symlink("link_b", os.path.join(root, "link_a"))       # 链接套链接
    os.symlink(os.path.join("..", "outside", "secret.txt"),
               os.path.join(root, "link_b"))
    os.makedirs(os.path.join(root, "mnt"))                   # 挂载点（跨挂载测试用）
    return {"base": base, "root": root, "outside": outside, "evil": evil}


def make_cases(paths: dict) -> list:
    """(名称, 攻击路径, 期望读到的越界内容 or None) 三元组列表。"""
    return [
        # --- 多级相对路径 ---
        ("rel-1 单级 ../", "../outside/secret.txt", SECRET),
        ("rel-2 多级 ../../", "sub/../../../outside/secret.txt", SECRET),
        ("rel-3 掺杂 ./", "./../../outside/secret.txt", SECRET),
        ("rel-4 前缀兄弟目录", "../sandbox_evil/evil.txt", EVIL),
        # --- 编码后的分隔符 ---
        ("enc-1 %2e%2e%2f", "%2e%2e%2foutside%2fsecret.txt", SECRET),
        ("enc-2 ..%2f", "..%2foutside%2fsecret.txt", SECRET),
        ("enc-3 混合编码", "%2e%2e/outside/secret.txt", SECRET),
        # --- 符号链接 ---
        ("sym-1 链接指向外部", "link_out", SECRET),
        ("sym-2 链接套链接", "link_a", SECRET),
        ("sym-3 相对路径+链接", "sub/../link_out", SECRET),
        # --- 大小写差异（大小写不敏感 FS 上生效）---
        ("case-1 大写目录", "../OUTSIDE/secret.txt", SECRET),
        ("case-2 混合大小写", "../Outside/Secret.TXT", SECRET),
        # --- 其他必须拒绝的情形 ---
        ("abs-1 绝对路径", os.path.join(paths["outside"], "secret.txt"), SECRET),
        ("nul-1 NUL 字节", "ok.txt\x00.txt", None),
        ("nf-1 沙箱外不存在文件", "../outside/missing.txt", None),
        ("dir-1 沙箱根本身", ".", None),
        ("dir-2 子目录本身", "sub", None),
        ("dir-3 解析回根目录", "sub/..", None),
        ("long-1 分量超过 NAME_MAX", "a" * 300 + ".txt", None),
        ("long-2 深层路径接近 PATH_MAX", "/".join(["d"] * 3000), None),
    ]


def try_vulnerable(root: str, attack: str):
    """返回 (是否得逞, 说明)。"""
    try:
        with vulnerable_sandbox.open_in_sandbox(root, attack) as f:
            data = f.read().decode(errors="replace")
        return True, f"读到 {data!r}"
    except Exception as exc:
        return False, f"被拒绝 ({type(exc).__name__})"


def try_secure(sandbox: "secure_sandbox.Sandbox", attack: str):
    """返回 (是否被正确拒绝, 说明)。"""
    try:
        with sandbox.open(attack) as f:
            data = f.read().decode(errors="replace")
        return False, f"!!! 绕过成功，读到 {data!r}"
    except secure_sandbox.SandboxError as exc:
        return True, f"拒绝: {type(exc).__name__}"
    except (FileNotFoundError, NotADirectoryError):
        return True, "拒绝: 文件不存在（未越界）"
    except OSError as exc:
        return True, f"拒绝: OSError({exc.errno})"


def run_mount_case() -> tuple:
    """跨挂载点用例：在沙箱内挂 tmpfs 放机密，验证被 st_dev 检查拒绝。"""
    helper = r'''
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "src"))
import secure_sandbox
base, root = sys.argv[2], sys.argv[3]
os.system("mount -t tmpfs tmpfs " + os.path.join(root, "mnt"))
with open(os.path.join(root, "mnt", "secret.txt"), "w") as f:
    f.write("MOUNTED-SECRET")
sb = secure_sandbox.Sandbox(root)
try:
    sb.open("mnt/secret.txt")
    print("BYPASSED")
except secure_sandbox.PathEscapeError as exc:
    print("REJECTED")
'''
    here = os.path.dirname(os.path.abspath(__file__))
    base = tempfile.mkdtemp(prefix="sandbox-mount-")
    try:
        root = os.path.join(base, "sandbox")
        os.makedirs(os.path.join(root, "mnt"))
        proc = subprocess.run(
            ["unshare", "-rm", sys.executable, "-c", helper, here, base, root],
            capture_output=True, text=True, timeout=60,
        )
        out = (proc.stdout or "").strip()
        if proc.returncode != 0 or not out:
            return None, f"跳过（unshare 不可用: {proc.stderr.strip()[:80]}）"
        return out == "REJECTED", out
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"跳过（{exc}）"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    base = tempfile.mkdtemp(prefix="sandbox-bypass-")
    try:
        paths = build_fixtures(base)
        root = paths["root"]
        sandbox = secure_sandbox.Sandbox(root)
        cases = make_cases(paths)

        print(f"沙箱根: {root}\n")
        print(f"{'用例':<26} {'有缺陷实现':<34} {'修复后实现'}")
        print("-" * 96)
        all_rejected = True
        for name, attack, _ in cases:
            v_pwned, v_msg = try_vulnerable(root, attack)
            s_ok, s_msg = try_secure(sandbox, attack)
            all_rejected &= s_ok
            v_cell = ("绕过成功 " + v_msg) if v_pwned else v_msg
            print(f"{name:<26} {v_cell:<34} {s_msg}")

        print("-" * 96)
        m_ok, m_msg = run_mount_case()
        label = {True: "拒绝: PathEscapeError", False: "!!! 绕过成功", None: m_msg}[m_ok]
        print(f"{'mnt-1 跨挂载点(tmpfs)':<26} {'(需独立命名空间)':<34} {label}")
        if m_ok is False:
            all_rejected = False

        print()
        if all_rejected:
            print("结论：修复后实现拒绝了全部绕过用例。")
            return 0
        print("结论：存在未被拒绝的用例！")
        return 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
