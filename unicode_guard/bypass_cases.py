# -*- coding: utf-8 -*-
"""绕过用例集：同形替换、组合顺序调换、零宽字符插入。

运行: python3 bypass_cases.py
每个用例断言两件事：
  1. 攻击串与目标串在 canonical_key 下等价（朴素比较会被绕过，本库不会）；
  2. 期望的风险类型全部被检出。
"""

from unicode_guard import (
    Config, IssueKind, Policy, analyze, canonical_key, equivalent,
)

ZWSP = "\u200B"   # 零宽空格
ZWNJ = "\u200C"  # 零宽不连字
ZWJ = "\u200D"   # 零宽连字
BOM = "\uFEFF"   # 零宽不换行空格/BOM
SHY = "\u00AD"   # 软连字符
TAG_A = "\U000E0061"   # 标签字符 TAG LATIN SMALL LETTER A
VS1 = "\uFE00"         # 变体选择符-1

# 组合顺序调换：规范顺序 a+U+0304+U+0301 vs 乱序 a+U+0301+U+0304
CANONICAL_ORDER = "a\u0323\u0301"
SWAPPED_ORDER = "a\u0301\u0323"

CASES = [
    # (用例名, 目标, 攻击串, 期望命中的问题类型)
    ("西里尔а替换",      "admin",  "аdmin",       {IssueKind.CONFUSABLE, IssueKind.MIXED_SCRIPT}),
    ("西里尔多字符替换",  "admin",  "аdmіn",       {IssueKind.CONFUSABLE, IssueKind.MIXED_SCRIPT}),
    ("希腊ο替换",        "root",   "rοοt",         {IssueKind.CONFUSABLE, IssueKind.MIXED_SCRIPT}),
    ("希腊全词伪装",      "system", "ѕуѕtеm",      {IssueKind.CONFUSABLE, IssueKind.MIXED_SCRIPT}),
    ("组合顺序调换",      "café",   "café",        {IssueKind.NON_NFC}),
    ("双组合符乱序",      CANONICAL_ORDER, SWAPPED_ORDER, {IssueKind.NON_NFC}),
    ("零宽空格插入",      "admin",  "adm" + ZWSP + "in",  {IssueKind.INVISIBLE}),
    ("ZWNJ插入",         "admin",  "ad" + ZWNJ + "min",   {IssueKind.INVISIBLE}),
    ("ZWJ插入",          "root",   "ro" + ZWJ + "ot",     {IssueKind.INVISIBLE}),
    ("BOM插入",          "test",   BOM + "test",          {IssueKind.INVISIBLE}),
    ("软连字符插入",      "hello",  "hel" + SHY + "lo",   {IssueKind.INVISIBLE}),
    ("标签字符注入",      "admin",  "admin" + TAG_A,      {IssueKind.INVISIBLE}),
    ("变体选择符注入",    "user",   "us" + VS1 + "er",    {IssueKind.INVISIBLE}),
    ("大小写折叠",        "Admin",  "ADMIN",              set()),
    ("兼容全角字符",      "user1",  "ｕｓｅｒ１",                set()),
    ("组合攻击:同形+零宽", "admin",  "аdm" + ZWSP + "in", {IssueKind.CONFUSABLE, IssueKind.INVISIBLE}),
]


def main() -> int:
    cfg = Config(invisible_policy=Policy.REJECT,
                 confusable_policy=Policy.WARN,
                 mixed_script_policy=Policy.WARN)
    failures = 0
    print(f"{'用例':<20}{'等价目标':<8}{'检出':<6}{'放行':<6}命中类型")
    print("-" * 80)
    for name, target, attack, expect_kinds in CASES:
        report = analyze(attack, cfg)
        got_kinds = {i.kind for i in report.issues}
        eq = equivalent(attack, target)
        detected = expect_kinds <= got_kinds
        ok = eq and detected
        failures += 0 if ok else 1
        kinds = ",".join(sorted(k.value for k in got_kinds)) or "-"
        print(f"{name:<20}{'是' if eq else '否':<8}"
              f"{'是' if detected else '否':<6}"
              f"{'是' if report.ok else '否':<6}{kinds}")
        if not eq:
            print(f"    [失败] 等价性不成立: key(attack)={report.canonical!r} "
                  f"key(target)={canonical_key(target)!r}")
        if not detected:
            print(f"    [失败] 未检出期望类型: "
                  f"{sorted(k.value for k in expect_kinds)}")
    print("-" * 80)
    print(f"共 {len(CASES)} 例, 失败 {failures} 例")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
