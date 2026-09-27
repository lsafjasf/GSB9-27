"""绕过用例集与测试夹具（被 demo 与回归测试共用）。

目录布局（base 为临时目录）：
    base/
      secret.txt                  <- 沙箱外的机密文件（攻击目标）
      sandbox_secret/secret.txt   <- 前缀混淆兄弟目录
      Sandbox/evil.txt            <- 与 root 同名仅大小写不同的兄弟目录
      a/
        sandbox/                  <- 沙箱根 root
          ok.txt                  <- 合法文件
          sub/deep.txt
          link_out  -> base/secret.txt     （符号链接指向外部）
          link_a    -> link_b -> 外部      （链接套链接）
          up        -> ..                  （指向上级的链接）
          proc_link -> /proc               （跨挂载点）
"""

import os
import tempfile

SECRET_CONTENT = b"TOP-SECRET-OUTSIDE-SANDBOX"


def build_fixture():
    base = tempfile.mkdtemp(prefix="pathguard_")
    root = os.path.join(base, "a", "sandbox")
    os.makedirs(os.path.join(root, "sub"))

    with open(os.path.join(base, "secret.txt"), "wb") as f:
        f.write(SECRET_CONTENT)
    with open(os.path.join(root, "ok.txt"), "wb") as f:
        f.write(b"hello sandbox")
    with open(os.path.join(root, "sub", "deep.txt"), "wb") as f:
        f.write(b"deep file")

    # 前缀混淆兄弟目录：名字以 sandbox 开头
    sibling = os.path.join(base, "a", "sandbox_secret")
    os.makedirs(sibling)
    with open(os.path.join(sibling, "secret.txt"), "wb") as f:
        f.write(SECRET_CONTENT)

    # 与 root 同名、仅大小写不同的兄弟目录
    case_sibling = os.path.join(base, "a", "Sandbox")
    os.makedirs(case_sibling)
    with open(os.path.join(case_sibling, "evil.txt"), "wb") as f:
        f.write(SECRET_CONTENT)

    # 符号链接：指向外部 / 链接套链接 / 指向上级 / 跨挂载点
    os.symlink(os.path.join(base, "secret.txt"), os.path.join(root, "link_out"))
    os.symlink("link_b", os.path.join(root, "link_a"))
    os.symlink(os.path.join(base, "secret.txt"), os.path.join(root, "link_b"))
    os.symlink("..", os.path.join(root, "up"))
    os.symlink("../..", os.path.join(root, "upup"))
    os.symlink("/proc", os.path.join(root, "proc_link"))

    return base, root


def make_cases(base, root):
    """返回 [(用例名, 用户输入路径), ...]，全部为应被拒绝的绕过尝试。"""
    sibling_secret = os.path.join(base, "a", "sandbox_secret", "secret.txt")
    case_evil = os.path.join(base, "a", "Sandbox", "evil.txt")
    return [
        ("符号链接指向外部", "link_out"),
        ("链接套链接 a->b->外部", "link_a"),
        ("多级相对(全编码) %2e%2e%2f x2", "%2e%2e%2f%2e%2e%2fsecret.txt"),
        ("多级相对(大写hex) %2E%2E%2F x2", "%2E%2E%2F%2E%2E%2Fsecret.txt"),
        ("多级相对(编码+原始混合)", "%2e%2e/%2e%2e%2fsecret.txt"),
        ("多级相对(经upup链接跳两级)", "upup/secret.txt"),
        ("前缀混淆(兄弟目录绝对路径)", sibling_secret),
        ("大小写差异(兄弟目录绝对路径)", case_evil),
        ("跨挂载点 /proc", "proc_link/self/status"),
        ("绝对路径直达外部", os.path.join(base, "secret.txt")),
    ]


def make_robustness_cases():
    """非绕过类的健壮性用例：(用例名, 输入, 期望)。在测试内解析异常类型。"""
    return [
        ("不存在的文件(沙箱内)", "no_such_file.txt", "FileNotFoundError"),
        ("不存在的文件(越界)", "../no_such.txt", "PathEscapeError"),
        ("目录本身 '.'", ".", "DIR_OK"),
        ("目录本身 '..'", "..", "PathEscapeError"),
        ("路径过长(6000字符)", "a" * 6000, "OSError|InvalidPathError"),
        ("NUL 字节", "ok.txt\x00.png", "InvalidPathError"),
    ]
