#!/usr/bin/env python3
"""稳定复现“两个看起来一样的键被判成不同”。

旧实现（线上行为）：只做 key.lower() 后直接比较。
本脚本不依赖修复后的代码，纯粹演示旧实现在哪四类输入上出错，
便于在修复前先确认问题真实存在：

    python3 tests/reproduce_bug.py

退出码：
    0  旧实现的问题被稳定复现（至少一对“看起来一样”的键被判不同）
    1  问题未能复现（环境异常，例如脚本被改坏）
"""

from __future__ import annotations

import sys

# ---- 旧实现：修复前的比较方式（保持原样，仅用于复现问题） --------------------

def old_normalize(key: str) -> str:
    return key.lower()


def old_equal(a: str, b: str) -> bool:
    return old_normalize(a) == old_normalize(b)


# ---- 复现用例：四类“看起来一样”的键 -----------------------------------------
# 每一组内的字符串肉眼等价，但覆盖不同的 Unicode 编码现象。

CASES = [
    (
        "大小写变体",
        ["ABC", "abc", "Abc"],
    ),
    (
        "全角 / 半角",
        ["ABC123", "\uff21\uff22\uff23\uff11\uff12\uff13"],  # ＡＢＣ１２３
    ),
    (
        "组合字符（预组合 vs 基字符+组合记号）",
        ["\u00e9", "e\u0301"],  # é(U+00E9) vs e + U+0301
    ),
    (
        "带变音符号（café 的各种写法，含去音符形式）",
        ["caf\u00e9", "cafe\u0301", "CAF\u00c9", "Caf\u00c9", "cafe"],
    ),
]


def main() -> int:
    bug_reproduced = False

    for group_name, variants in CASES:
        print(f"[{group_name}]")
        anchor = variants[0]
        for other in variants[1:]:
            same = old_equal(anchor, other)
            flag = "OK  判为相同" if same else "BUG 判为不同"
            if not same:
                bug_reproduced = True
            print(
                f"  {flag}: {anchor!r} (码点 "
                f"{[hex(ord(c)) for c in anchor]})  vs  "
                f"{other!r} (码点 {[hex(ord(c)) for c in other]})"
            )
        # 旧实现下每组的“等价类”数（>1 即说明发生了错误拆分）
        partitions = len({old_normalize(v) for v in variants})
        print(f"  旧实现把这一组拆成了 {partitions} 个不同的键")
        print()

    if bug_reproduced:
        print("问题已复现：仅做 lower() 会把全角、组合/预组合字符、")
        print("变音符号变体错误地判为不同的键。修复见 keycmp/normalizer.py。")
        return 0

    print("未能复现问题，请检查脚本或运行环境。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
