# -*- coding: utf-8 -*-
"""等价类断言 + 策略行为自测。运行: python3 -m unittest test_unicode_guard -v"""

import unittest

from unicode_guard import (
    Config, IssueKind, Policy, analyze, canonical_key, display_form,
    equivalent, is_invisible, skeleton, storage_form, strip_invisible,
)


# ---------------------------------------------------------------------------
# 等价类划分：同一列表内的字符串必须两两等价，跨列表必须两两不等价
# ---------------------------------------------------------------------------

EQUIVALENCE_CLASSES = [
    # 类 1: admin 的各种伪装/变形
    ["admin", "ADMIN", "Admin", "аdmin", "аdmіn",
     "adm\u200Bin", "\uFEFFadmin", "ａｄｍｉｎ"],
    # 类 2: café 的预组合/分解/乱序形式
    ["café", "café", "CAFÉ", "café"],
    # 类 3: 组合符顺序调换
    ["a\u0323\u0301", "a\u0301\u0323"],
    # 类 4: root
    ["root", "ROOT", "rοοt", "ro\u200Dot"],
    # 类 5: 与以上都不等价的普通串
    ["user1", "ｕｓｅｒ１", "USer1"],
]


class TestEquivalenceRelation(unittest.TestCase):
    """等价性必须自洽：自反、对称、传递，且与等价类划分一致。"""

    def setUp(self):
        self.all_strings = [s for cls in EQUIVALENCE_CLASSES for s in cls]

    def test_reflexive(self):
        for s in self.all_strings:
            self.assertTrue(equivalent(s, s), repr(s))

    def test_symmetric(self):
        for a in self.all_strings:
            for b in self.all_strings:
                self.assertEqual(equivalent(a, b), equivalent(b, a),
                                 f"{a!r} vs {b!r}")

    def test_transitive(self):
        for a in self.all_strings:
            for b in self.all_strings:
                for c in self.all_strings:
                    if equivalent(a, b) and equivalent(b, c):
                        self.assertTrue(equivalent(a, c),
                                        f"{a!r} {b!r} {c!r}")

    def test_partition_matches_key_grouping(self):
        """两两等价关系导出的分组 == canonical_key 分组 == 期望划分。"""
        # 1) 两两等价分组
        groups_by_relation = []
        for s in self.all_strings:
            for g in groups_by_relation:
                if equivalent(s, g[0]):
                    g.append(s)
                    break
            else:
                groups_by_relation.append([s])
        # 2) canonical_key 分组
        groups_by_key = {}
        for s in self.all_strings:
            groups_by_key.setdefault(canonical_key(s), []).append(s)
        # 3) 期望划分
        expected = sorted(sorted(c) for c in EQUIVALENCE_CLASSES)
        self.assertEqual(sorted(sorted(g) for g in groups_by_relation), expected)
        self.assertEqual(sorted(sorted(g) for g in groups_by_key.values()),
                         expected)

    def test_cross_class_inequivalent(self):
        for i, cls_a in enumerate(EQUIVALENCE_CLASSES):
            for j, cls_b in enumerate(EQUIVALENCE_CLASSES):
                if i >= j:
                    continue
                for a in cls_a:
                    for b in cls_b:
                        self.assertFalse(equivalent(a, b), f"{a!r} == {b!r}")

    def test_key_idempotent(self):
        """canonical_key 幂等：键的键仍是键本身。"""
        for s in self.all_strings:
            k = canonical_key(s)
            self.assertEqual(canonical_key(k), k, repr(s))


class TestNormalizationForms(unittest.TestCase):
    """比较/存储/展示三环节的形式约定。"""

    def test_storage_strips_invisible_keeps_visible(self):
        s = "аdm\u200Bin"
        self.assertEqual(storage_form(s), "аdmin")   # 可见字符保留
        self.assertEqual(display_form(s), s)          # 展示保持原样(NFC)
        self.assertEqual(canonical_key(s), "admin")   # 比较键已折叠

    def test_storage_is_nfc(self):
        decomposed = "café"
        self.assertEqual(storage_form(decomposed), "café")

    def test_display_does_not_fold_case(self):
        self.assertEqual(display_form("Admin"), "Admin")


class TestPolicies(unittest.TestCase):
    ATTACK_INVISIBLE = "adm\u200Bin"
    ATTACK_CONFUSABLE = "аdmin"

    def test_reject(self):
        cfg = Config(invisible_policy=Policy.REJECT)
        self.assertFalse(analyze(self.ATTACK_INVISIBLE, cfg).ok)

    def test_warn_allows_but_reports(self):
        cfg = Config(invisible_policy=Policy.WARN)
        r = analyze(self.ATTACK_INVISIBLE, cfg)
        self.assertTrue(r.ok)
        self.assertIn(IssueKind.INVISIBLE, {i.kind for i in r.issues})

    def test_replace_sanitizes(self):
        cfg = Config(invisible_policy=Policy.REPLACE)
        r = analyze(self.ATTACK_INVISIBLE, cfg)
        self.assertTrue(r.ok)
        self.assertEqual(r.sanitized, "admin")

    def test_confusable_reject(self):
        cfg = Config(confusable_policy=Policy.REJECT)
        self.assertFalse(analyze(self.ATTACK_CONFUSABLE, cfg).ok)

    def test_clean_string_passes_all(self):
        r = analyze("normal_user-1", Config())
        self.assertTrue(r.ok)
        self.assertEqual(r.issues, [])


class TestDetectors(unittest.TestCase):
    def test_invisible_set(self):
        for ch in "\u200B\u200C\u200D\uFEFF\u00AD\uFE00\U000E0061\x00\x07":
            self.assertTrue(is_invisible(ch), f"U+{ord(ch):04X}")
        for ch in "aA1 \t\n\r":
            self.assertFalse(is_invisible(ch), f"U+{ord(ch):04X}")

    def test_skeleton(self):
        self.assertEqual(skeleton("аdmin"), "admin")
        self.assertEqual(skeleton("ѕуѕtеm"), "system")

    def test_strip_invisible(self):
        self.assertEqual(strip_invisible("a\u200Bb\uFEFFc"), "abc")


if __name__ == "__main__":
    unittest.main()
