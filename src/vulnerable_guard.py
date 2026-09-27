"""修复前的路径校验实现（故意保留的典型缺陷版本，仅用于演示与回归对比）。

缺陷清单：
1. 只做字符串前缀比较，且 root 后不带分隔符 -> 兄弟目录前缀混淆
   （.../sandbox_secret 能通过 .../sandbox 的前缀检查）。
2. 比较前做 lower()（从 Windows 移植来的习惯）-> 大小写差异混淆。
3. 不解析符号链接（没有 realpath）-> 沙箱内的 symlink 可指向外部。
4. 先校验、后解码（unquote 在检查之后）-> 编码分隔符 %2e%2f 绕过。
5. 用 ".." 字面匹配防相对路径 -> 编码后或经 symlink 的多级跳转均可绕过。
"""

import os
import urllib.parse


def vulnerable_open(root, user_path):
    if ".." in user_path:
        raise PermissionError("parent traversal detected")
    candidate = os.path.normpath(os.path.join(root, user_path))
    # 缺陷1+2：无分隔符的字符串前缀比较，且做了小写化
    if not candidate.lower().startswith(root.lower()):
        raise PermissionError("escape detected")
    # 缺陷4：校验之后才做 URL 解码，检查与实际打开的不是同一个字符串
    return open(urllib.parse.unquote(candidate), "rb")
