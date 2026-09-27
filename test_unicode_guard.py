"""自测：等价类断言 + 绕过用例集检出 + 策略行为 + 检出报告。

运行：
  python3 test_unicode_guard.py            # 单元测试 + 检出报告
  python3 test_unicode_guard.py -v         # unittest 详细模式
  python3 test_unicode_guard.py --demo     # 只打印绕过用例检出报告
"""

import sys
import unittest

from unicode_guard import (
    Action, FindingType, DEFAULT_POLICY, SafetyError,
    UsernameBlacklist, compare_key, display_form, storage_form,
    equivalent, inspect, scan,
)

ZWSP = "​"          # 零宽空格
ZWJ = "‍"           # 零宽连接符
ZWNJ = "‌"          # 零宽非连接符
WJ = "⁠"            # 单词连接符
RLO = "‮"           # 从右向左覆盖
LRI = "⁦"           # 从左向右隔离
PDI = "⁩"           # 隔离终止
TAG_A = "\U000E0061"     # 标签字符 TAG LATIN SMALL LETTER A
VS16 = "️"          # 变体选择符-16
NONCHAR = "￾"      # 非字符码位
GRAVE = "̀"          # 组合重音符 CCC=230
ACUTE = "́"          # 组合尖音符 CCC=230
COMMA_ABOVE_R = "̕"  # 组合右上逗号 CCC=232

# ---------------------------------------------------------------------------
# 绕过用例集（B01..B20）
# ---------------------------------------------------------------------------

BYPASS_CASES = [
    # (编号, 输入, 说明, 期望检出的 FindingType)
    ("B01", "аdmin",            "同形替换：西里尔 а 替换拉丁 a",      {FindingType.MIXED_SCRIPT}),
    ("B02", "admіn",            "同形替换：西里尔 і 替换拉丁 i",      {FindingType.MIXED_SCRIPT}),
    ("B03", "αdmin",            "同形替换：希腊 α 替换拉丁 a",        {FindingType.MIXED_SCRIPT}),
    ("B04", "аdмⅰn",           "同形混用：西里尔 а/м + 罗马数字 ⅰ",  {FindingType.MIXED_SCRIPT}),
    ("B05", "ＲＯＯＴ",         "全角大写拼出 ROOT",                  {FindingType.FOLDED}),
    ("B06", "cafe" + ACUTE,     "分解形式 café（e + 组合尖音符）",    set()),
    ("B07", "café",             "预组合 é（与 B06 同键）",            set()),
    ("B08", "a" + COMMA_ABOVE_R + GRAVE, "组合顺序调换：黑名单序列的重排形式", set()),
    ("B09", "ad" + ZWSP + "min",          "零宽插入：零宽空格",       {FindingType.INVISIBLE}),
    ("B10", "a" + ZWJ + "dmin",           "零宽插入：零宽连接符",     {FindingType.INVISIBLE}),
    ("B11", "admi" + WJ + "n",            "零宽插入：单词连接符",     {FindingType.INVISIBLE}),
    ("B12", "admi" + RLO + "n",           "插入 RLO 双向覆盖",        {FindingType.BIDI_CONTROL}),
    ("B13", LRI + "admin" + PDI,          "双向隔离符包裹",           {FindingType.BIDI_CONTROL}),
    ("B14", "adm" + TAG_A + "in",         "零宽插入：标签字符",       {FindingType.INVISIBLE}),
    ("B15", "admin" + VS16,               "追加变体选择符",           {FindingType.VARIATION_SELECTOR}),
    ("B16", "adm" + NONCHAR + "in",       "插入非字符码位",           {FindingType.NONCHARACTER}),
    ("B17", GRAVE + "admin",              "前置游离组合符",           {FindingType.DANGLING_MARK}),
    ("B18", "ｒｏｏｔ",         "全角小写拼出 root",                  {FindingType.FOLDED}),
    ("B19", "r" + ZWNJ + "oot",           "零宽插入：零宽非连接符",   {FindingType.INVISIBLE}),
    ("B20", "rοοt",             "同形替换：希腊 ο 替换拉丁 o",        {FindingType.MIXED_SCRIPT}),
]

BLACKLIST = ["admin", "root", "café", "cafe" + ACUTE, GRAVE + "admin",
             "a" + GRAVE + COMMA_ABOVE_R]  # B08 的规范顺序形式

# 三种演示策略
POLICY_STRICT = {ft: Action.REJECT for ft in FindingType}
POLICY_LENIENT = {ft: Action.WARN for ft in FindingType}
POLICY_DEFAULT = dict(DEFAULT_POLICY)  # 混排/bidi REJECT，不可见/变体选择符 REPLACE

DEMO_POLICIES = [
    ("严格(全部REJECT)", POLICY_STRICT),
    ("宽松(全部WARN)", POLICY_LENIENT),
    ("默认(混合策略)", POLICY_DEFAULT),
]

# ---------------------------------------------------------------------------
# 等价类划分（每个集合内部互相等价，集合之间互不等价）
# ---------------------------------------------------------------------------

EQUIV_CLASSES = [
    {
        "admin", "ADMIN", "Admin", "аdmin", "admіn", "αdmin", "аdмⅰn",
        "ＡＤＭＩＮ", "ａｄｍｉｎ",
        "ad" + ZWSP + "min", "a" + ZWJ + "dmin", "admi" + WJ + "n",
        "adm" + TAG_A + "in", "admin" + VS16, "adm" + NONCHAR + "in",
        GRAVE + "admin", LRI + "admin" + PDI,
    },
    {
        "café", "cafe" + ACUTE, "CAFÉ", "ｃａｆé",
        "cafe" + ACUTE + ZWSP, "ｃａｆ" + "e" + ACUTE,
    },
    {
        "a" + GRAVE + COMMA_ABOVE_R,   # 规范排序后 == 下一行
        "a" + COMMA_ABOVE_R + GRAVE,   # 组合顺序调换，规范化后相同
    },
    {"root", "ROOT", "ｒｏｏｔ", "ＲＯＯＴ", "r" + ZWSP + "oot", "rοοt"},
    {"user", "USER", "ｕｓｅｒ"},
]

ALL_STRINGS = sorted({s for cls in EQUIV_CLASSES for s in cls})


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------


class TestEquivalenceClasses(unittest.TestCase):
    """等价性自洽：compare_key 相等关系必须与等价类划分一致。"""

    def test_intra_class_equal_keys(self):
        for cls in EQUIV_CLASSES:
            keys = {compare_key(s) for s in cls}
            self.assertEqual(len(keys), 1, f"类内键不唯一: {keys}")

    def test_inter_class_distinct_keys(self):
        reps = [next(iter(cls)) for cls in EQUIV_CLASSES]
        keys = [compare_key(r) for r in reps]
        self.assertEqual(len(keys), len(set(keys)), f"类间键冲突: {keys}")

    def test_relation_matches_partition(self):
        """对样本全集验证：equivalent(a,b) 当且仅当 a,b 同属一个等价类。"""
        owner = {}
        for idx, cls in enumerate(EQUIV_CLASSES):
            for s in cls:
                owner[s] = idx
        for a in ALL_STRINGS:
            for b in ALL_STRINGS:
                self.assertEqual(
                    equivalent(a, b), owner[a] == owner[b],
                    f"等价关系与划分不一致: {a!r} vs {b!r}")

    def test_equivalence_axioms(self):
        """自反、对称、传递。"""
        for a in ALL_STRINGS:
            self.assertTrue(equivalent(a, a), "自反性失败")
            for b in ALL_STRINGS:
                self.assertEqual(equivalent(a, b), equivalent(b, a), "对称性失败")
                for c in ALL_STRINGS:
                    if equivalent(a, b) and equivalent(b, c):
                        self.assertTrue(equivalent(a, c), "传递性失败")

    def test_idempotent_key(self):
        """比较键幂等：compare_key(compare_key(s)) == compare_key(s)。"""
        for s in ALL_STRINGS:
            self.assertEqual(compare_key(compare_key(s)), compare_key(s))


class TestBypassDetection(unittest.TestCase):
    """绕过用例集：同形替换、组合顺序调换、零宽插入都必须被检出。"""

    def test_findings_emitted(self):
        for cid, s, desc, expected in BYPASS_CASES:
            with self.subTest(case=cid, desc=desc):
                found = {f.type for f in scan(s)}
                self.assertTrue(expected <= found,
                                f"{cid} 缺少发现: 期望{expected} 实际{found}")

    def test_all_cases_blocked_by_blacklist(self):
        """无论策略如何，每个用例都被拦截（拒绝或归一化后命中黑名单）。"""
        for name, policy in DEMO_POLICIES:
            bl = UsernameBlacklist(BLACKLIST, policy)
            for cid, s, desc, _ in BYPASS_CASES:
                with self.subTest(policy=name, case=cid):
                    try:
                        insp = bl.check(s)
                        blocked = insp.blacklisted
                    except SafetyError:
                        blocked = True  # 被策略拒绝，同样视为拦截
                    self.assertTrue(blocked, f"{cid} 在策略[{name}]下绕过黑名单")

    def test_combining_order_swap(self):
        """组合顺序调换：规范等价的组合序列归一化后同键。"""
        self.assertEqual(compare_key("café"), compare_key("cafe" + ACUTE))
        self.assertEqual(compare_key("a" + GRAVE + COMMA_ABOVE_R),
                         compare_key("a" + COMMA_ABOVE_R + GRAVE))

    def test_zero_width_insertion_removed_in_key(self):
        for zw in (ZWSP, ZWJ, ZWNJ, WJ, TAG_A, VS16, NONCHAR):
            self.assertEqual(compare_key("ad" + zw + "min"), "admin",
                             f"未剔除 U+{ord(zw):04X}")

    def test_homoglyph_skeleton_in_key(self):
        for fake in ("аdmin", "admіn", "αdmin", "аdмⅰn"):
            self.assertEqual(compare_key(fake), "admin")


class TestPolicyBehavior(unittest.TestCase):
    def test_reject_raises(self):
        with self.assertRaises(SafetyError) as ctx:
            inspect("аdmin", POLICY_STRICT)
        self.assertTrue(ctx.exception.inspection.rejected)
        self.assertEqual(ctx.exception.inspection.reject_reasons[0].type,
                         FindingType.MIXED_SCRIPT)

    def test_warn_passes_with_findings(self):
        insp = inspect("аdmin", POLICY_LENIENT)
        self.assertFalse(insp.rejected)
        self.assertEqual(insp.sanitized, "аdmin")   # WARN 不修改原文
        self.assertTrue(insp.warnings)
        self.assertEqual(insp.compare_key, "admin")  # 但比较键仍然安全

    def test_replace_strips_invisible(self):
        insp = inspect("ad" + ZWSP + "min", POLICY_DEFAULT)
        self.assertEqual(insp.sanitized, "admin")
        self.assertEqual(insp.compare_key, "admin")
        self.assertTrue(any(f.type is FindingType.INVISIBLE for f in insp.warnings))

    def test_replace_still_rejects_mixed_script(self):
        with self.assertRaises(SafetyError):
            inspect("аdmin", POLICY_DEFAULT)

    def test_pure_cyrillic_not_flagged(self):
        """纯西里尔用户名（非混排）不应误报 MIXED_SCRIPT。"""
        self.assertNotIn(FindingType.MIXED_SCRIPT,
                         {f.type for f in scan("иван")})

    def test_pure_latin_with_accent_not_flagged(self):
        self.assertEqual(scan("café"), [])

    def test_display_and_storage_forms(self):
        s = "cafe" + ACUTE
        self.assertEqual(display_form(s), "café")
        self.assertEqual(storage_form(s), "café")
        self.assertEqual(display_form("аdmin"), "аdmin")  # 展示/存储保留原文


# ---------------------------------------------------------------------------
# 检出报告
# ---------------------------------------------------------------------------


def _cps(s: str) -> str:
    return " ".join(f"U+{ord(c):04X}" for c in s)


def render_report() -> str:
    lines = []
    out = lines.append
    out("=" * 78)
    out("绕过用例集检出报告  (黑名单: admin / root / café / cafe+́ / ̀admin / a+̀̕)")
    out("=" * 78)
    for cid, s, desc, _ in BYPASS_CASES:
        findings = scan(s)
        out(f"\n[{cid}] {desc}")
        out(f"  输入     : {s!r}")
        out(f"  码位     : {_cps(s)}")
        out(f"  比较键   : {compare_key(s)!r}")
        if findings:
            for f in findings:
                out(f"  发现     : {f.describe()}")
        else:
            out("  发现     : （无安全发现；属归一化等价命中）")
        for name, policy in DEMO_POLICIES:
            bl = UsernameBlacklist(BLACKLIST, policy)
            try:
                insp = bl.check(s)
                hit = "命中黑名单" if insp.blacklisted else "未命中"
                out(f"  策略[{name}] -> 放行({hit}), 净化后={insp.sanitized!r}")
            except SafetyError as e:
                kinds = ",".join(sorted({f.type.value for f in e.inspection.reject_reasons}))
                out(f"  策略[{name}] -> 拒绝({kinds})")
    out("\n" + "=" * 78)
    out("结论：全部用例均被检出——或被策略拒绝，或归一化后命中黑名单比较键。")
    out("=" * 78)
    return "\n".join(lines)


def print_demo() -> None:
    print(render_report())


def main() -> None:
    if "--demo" in sys.argv:
        print_demo()
        return
    verbosity = 2 if "-v" in sys.argv else 1
    argv = [sys.argv[0]] + (["-v"] if verbosity == 2 else [])
    result = unittest.TextTestRunner(verbosity=verbosity).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print()
    print_demo()
    if not result.wasSuccessful():
        sys.exit(1)


if __name__ == "__main__":
    main()
