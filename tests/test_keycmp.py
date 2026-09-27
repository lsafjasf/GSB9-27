#!/usr/bin/env python3
"""回归测试：修复后的键比较。

运行：
    python3 -m unittest tests.test_keycmp -v
或：
    python3 tests/test_keycmp.py
"""

from __future__ import annotations

import sys
import os
import sys
import unittest
from itertools import permutations, product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from keycmp import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    KeyNormalizer,
    keys_equal,
    normalize_key,
)

# ---- 等价类数据 -------------------------------------------------------------
# 同一个 frozenset 里的所有写法，规范化后必须落在同一个等价类。
# 四类必测现象：大小写、全角/半角、组合字符、变音符号。

EQUIVALENCE_CLASSES_ROOT = [
    # 1) 大小写变体
    {"ABC", "abc", "Abc", "aBc"},
    # 2) 全角 / 半角（含全角数字）
    {"ABC123", "abc123", "\uff21\uff22\uff23\uff11\uff12\uff13"},
    # 3) 组合字符：预组合 é vs e + U+0301
    {"\u00e9", "e\u0301", "\u00c9", "E\u0301"},
    # 4) 带变音符号：café 的全部常见写法 + 去音符形式（默认 accent-insensitive）
    {
        "caf\u00e9",          # café（预组合）
        "cafe\u0301",         # cafe + 组合重音
        "CAF\u00c9",          # CAFÉ
        "Caf\u00c9",          # CafÉ
        "cafe",               # 无重音
        "CAFE",
        "ＣＡＦＥ",            # 全角
    },
    # 5) casefold 才覆盖的大小写：德语 ß casefold 为 ss
    {"Stra\u00dfe", "strasse", "STRASSE", "STRA\u00dfE"},
    # 6) 兼容合字：NFKC + casefold 后 ﬃ(U+FB03) 与 ffi 等价
    {"\ufb03ce", "ffice", "FFICE"},
]

# 土耳其语区域下应等价的组
EQUIVALENCE_CLASSES_TR = [
    # İ(U+0130) -> i
    {"\u0130stanbul", "istanbul", "\u0130STANBUL"},
    # I(U+0049) -> ı(U+0131)
    {"I\u015f\u0131k", "\u0131\u015f\u0131k", "IŞIK"},
]


def _assert_equivalence_class(testcase: unittest.TestCase, members, locale: str):
    """一个等价类必须满足：自身等价 + 两两等价（由此保证传递性前提）。"""
    norm = KeyNormalizer(locale)
    forms = {norm.normalize(m) for m in members}
    testcase.assertEqual(
        len(forms), 1,
        msg=(
            f"locale={locale!r} 下这些键应同属一个等价类，却被拆成 "
            f"{len(forms)} 个：{sorted(forms)}\n"
            f"输入：{sorted(members)}"
        ),
    )
    canonical = next(iter(forms))
    for member in members:
        # 自反性
        testcase.assertTrue(norm.equal(member, member), f"自反性失败：{member!r}")
        testcase.assertEqual(norm.normalize(member), canonical)
    # 两两等价（对称 + 传递：任意 a~b、b~c 则 a~c）
    for a, b in permutations(members, 2):
        testcase.assertTrue(
            norm.equal(a, b),
            msg=f"locale={locale!r} 等价类内两两等价失败：{a!r} vs {b!r}",
        )


class EquivalenceClassTests(unittest.TestCase):
    def test_root_classes_collapse_to_one(self):
        for members in EQUIVALENCE_CLASSES_ROOT:
            with self.subTest(members=sorted(members)):
                _assert_equivalence_class(self, members, "root")

    def test_tr_classes_collapse_to_one(self):
        for members in EQUIVALENCE_CLASSES_TR:
            with self.subTest(members=sorted(members)):
                _assert_equivalence_class(self, members, "tr")

    def test_distinct_classes_stay_distinct(self):
        """不同等价类不能被错误合并（防止修复过度，例如丢字符过头）。"""
        norm = KeyNormalizer("root")
        representatives = [next(iter(c)) for c in EQUIVALENCE_CLASSES_ROOT]
        for a, b in permutations(representatives, 2):
            self.assertNotEqual(
                norm.normalize(a), norm.normalize(b),
                msg=f"不同语义的键被错误合并：{a!r} 与 {b!r}",
            )

    def test_basic_semantic_distinctions(self):
        norm = KeyNormalizer("root")
        for a, b in [("abc", "abd"), ("cafe", "cafel"), ("abc", "ab c")]:
            self.assertNotEqual(norm.normalize(a), norm.normalize(b))


class EquivalenceRelationAxiomsTests(unittest.TestCase):
    """对更广泛的采样输入直接验证等价关系的三条公理。"""

    SAMPLE_KEYS = [
        "abc", "ABC", "Abc",
        "\uff21\uff22\uff23",          # ＡＢＣ
        "caf\u00e9", "CAF\u00c9", "cafe\u0301", "cafe",
        "\u00e9", "e\u0301",
        "Stra\u00dfe", "strasse",
        "naive_Key_01", "NAIVE_KEY_01",
        "\u0130stanbul", "istanbul",
        "I\u015f\u0131k", "\u0131\u015f\u0131k",
    ]

    def test_reflexive(self):
        for locale in SUPPORTED_LOCALES:
            norm = KeyNormalizer(locale)
            for key in self.SAMPLE_KEYS:
                self.assertTrue(norm.equal(key, key), (locale, key))

    def test_symmetric(self):
        for locale in SUPPORTED_LOCALES:
            norm = KeyNormalizer(locale)
            for a, b in product(self.SAMPLE_KEYS, repeat=2):
                if norm.equal(a, b):
                    self.assertTrue(norm.equal(b, a), (locale, a, b))

    def test_transitive(self):
        for locale in SUPPORTED_LOCALES:
            norm = KeyNormalizer(locale)
            for a, b, c in product(self.SAMPLE_KEYS, repeat=3):
                if norm.equal(a, b) and norm.equal(b, c):
                    self.assertTrue(norm.equal(a, c), (locale, a, b, c))

    def test_normalization_is_idempotent(self):
        """规范化结果再次规范化必须不变（保证结果是稳定的代表元）。"""
        for locale in SUPPORTED_LOCALES:
            norm = KeyNormalizer(locale)
            for key in self.SAMPLE_KEYS:
                once = norm.normalize(key)
                self.assertEqual(once, norm.normalize(once), (locale, key))

    def test_invariant_under_unicode_reencodings(self):
        """等价判断不受 NFC/NFD/NFKC/NFKD 重新编码与大小写写法影响。"""
        import unicodedata
        norm = KeyNormalizer("root")
        seeds = ["caf\u00e9", "\u00e9clair", "Stra\u00dfe"]
        encodings = ("NFC", "NFD", "NFKC", "NFKD")
        for seed in seeds:
            variants = [unicodedata.normalize(f, seed) for f in encodings]
            variants += [seed.upper(), seed.lower()]
            for other in variants[1:]:
                self.assertEqual(
                    norm.normalize(variants[0]), norm.normalize(other),
                    msg=f"{seed!r} 的 {variants[0]!r} 与 {other!r} 应等价",
                )


class LocaleDifferenceTests(unittest.TestCase):
    """默认规则(root) 与可选规则(tr) 的差异必须显式且被测到。

    差异 1：I / ı
        root: 大写 I -> 小写 i（英语直觉），故 Işık 与 ışık 不等价
        tr  : 大写 I -> 无点 ı(U+0131)，故 Işık == ışık，且与 isik 不同
    差异 2：İ
        root: İ(U+0130) casefold 为 "i\u0307"，去记号后 == "i"
              —— root 下 İ 与 i 恰好同形（无点 i），但与 ı 不同
        tr  : İ 显式映射为 i
    结论：在 tr 下 "i" 与 "ı" 严格区分；root 下 I 并入 i。
    """

    def test_capital_I_merges_with_i_only_in_root(self):
        self.assertTrue(keys_equal("I", "i", locale="root"))
        self.assertFalse(keys_equal("I", "i", locale="tr"))
        self.assertTrue(keys_equal("I", "\u0131", locale="tr"))  # I == ı
        self.assertFalse(keys_equal("I", "\u0131", locale="root"))

    def test_dotted_capital_i(self):
        # İ(U+0130)：两种区域下都与 i 等价
        self.assertTrue(keys_equal("\u0130", "i", locale="root"))
        self.assertTrue(keys_equal("\u0130", "i", locale="tr"))
        # 但都不应与无点 ı 合并
        self.assertFalse(keys_equal("\u0130", "\u0131", locale="root"))
        self.assertFalse(keys_equal("\u0130", "\u0131", locale="tr"))

    def test_turkish_word_pair_differs_by_locale(self):
        word_a = "I\u015f\u0131k"   # Işık
        word_b = "\u0131\u015f\u0131k"  # ışık
        word_c = "i\u015f\u0131k"   # işık
        self.assertFalse(keys_equal(word_a, word_b, locale="root"))
        self.assertTrue(keys_equal(word_a, word_b, locale="tr"))
        # tr 下 ı 与 i 必须保持区分
        self.assertFalse(keys_equal(word_b, word_c, locale="tr"))

    def test_default_locale_is_root(self):
        self.assertEqual(DEFAULT_LOCALE, "root")
        self.assertEqual(normalize_key("ABC"), normalize_key("ABC", "root"))

    def test_invalid_locale_rejected(self):
        with self.assertRaises(ValueError):
            KeyNormalizer(locale="de")
        with self.assertRaises(ValueError):
            normalize_key("x", locale="")

    def test_non_string_rejected(self):
        with self.assertRaises(TypeError):
            KeyNormalizer().normalize(123)


class LookupAndDedupSmokeTests(unittest.TestCase):
    """字典查找与去重两个真实使用场景的冒烟测试。"""

    def test_casefold_lookup(self):
        table = {}
        norm = KeyNormalizer()
        key = "ＣＡＦÉ"
        table[norm.normalize(key)] = 42
        self.assertEqual(table.get(norm.normalize("cafe\u0301")), 42)
        self.assertEqual(table.get(norm.normalize("CAFE")), 42)

    def test_dedup_collapses_variants(self):
        raw = ["caf\u00e9", "CAFE", "cafe\u0301", "ＣＡＦＥ", "tea"]
        norm = KeyNormalizer()
        deduped = list({norm.normalize(k): k for k in raw})
        self.assertEqual(sorted(norm.normalize(k) for k in deduped),
                         ["cafe", "tea"])


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=2))
