"""有缺陷的沙箱路径校验实现（仅用于演示绕过，请勿在生产中使用）。

已知缺陷：
1. 只做词法 normpath，不解析符号链接（realpath）。
2. 前缀比较没有分隔符边界：/srv/sandbox_evil 能通过 /srv/sandbox 的检查。
3. 先校验、后做百分号解码，编码后的 ../ 可绕过。
4. 大小写不敏感文件系统上未做 normcase，直接字符串比较。
5. 校验的是用户路径、打开的也是用户路径，且校验与打开之间无防替换措施。
"""

from __future__ import annotations

import os
import urllib.parse


def open_in_sandbox(root: str, user_path: str):
    """返回沙箱内文件的二进制文件对象。存在多处缺陷，见模块 docstring。"""
    if "\x00" in user_path:
        raise ValueError("NUL byte in path")

    # 缺陷 1+2：纯词法拼接与前缀比较
    full = os.path.normpath(os.path.join(root, user_path))
    if not full.startswith(root):  # 缺陷 2：缺少分隔符边界
        raise PermissionError(f"path escapes sandbox: {user_path!r}")

    # 缺陷 3：校验之后才解码，%2e%2e%2f 在校验时还不是 ../
    full = urllib.parse.unquote(full)

    # 缺陷 5：直接打开用户控制的路径
    return open(full, "rb")
