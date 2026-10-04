#!/usr/bin/env python3
"""白名单校验库自测（标准库 unittest）。

运行: python3 -m unittest test_validator -v
"""

import unittest

from bypass_corpus import CORPUS, reject
from whitelist_validator import (
    RAW_INPUT_LIMIT,
    ValidationError,
    normalize,
    validate,
)


class TestLegalInputs(unittest.TestCase):
    """合法输入必须全部通过，且返回规范形式。"""

    CASES = {
        "username": ["abc", "a" * 20, "user_01", "z9_"],
        "slug": ["a", "hello-world", "a-b-c-d-e-f-g-h", "x" * 64],
        "filename": ["a.txt", "report_2024-01.csv", "x" * 53 + ".json"],
        "rel_path": ["a", "a/b/c/d/e", "docs/v1.2/read_me~1.txt"],
        "email": ["a@b.co", "user.name+tag@sub.example.com",
                  "u" * 64 + "@" + "a" * 60 + ".com"],
        "integer": ["0", "-1", "9223372036854775807", "-9223372036854775808"],
        "json": ['{"user_id":1,"tags":["a","b"],"ok":true,"note":null}',
                 "[" * 7 + "1" + "]" * 7,
                 '{"k":"' + "v" * 256 + '"}'],
    }

    def test_legal(self):
        for kind, samples in self.CASES.items():
            for sample in samples:
                with self.subTest(kind=kind, sample=sample[:40]):
                    self.assertEqual(validate(kind, sample), sample)

    def test_normalization_equivalence(self):
        # 编码等价写法规范化后按规范形式接受
        self.assertEqual(validate("username", "\uff41\uff44\uff4d\uff49\uff4e"), "admin")
        self.assertEqual(validate("integer", "\uff11\uff12\uff13"), "123")
        self.assertEqual(validate("rel_path", "a%2fb"), "a/b")
        self.assertEqual(validate("email", "user&#64;x.com"), "user@x.com")


class TestBoundaries(unittest.TestCase):
    """边界：恰好在上限的接受，越界的拒绝。"""

    def assert_ok(self, kind, value):
        self.assertEqual(validate(kind, value), value)

    def assert_bad(self, kind, value):
        with self.assertRaises(ValidationError):
            validate(kind, value)

    def test_username_length(self):
        self.assert_bad("username", "ab")          # 2 < 3
        self.assert_ok("username", "abc")          # 3
        self.assert_ok("username", "a" * 20)       # 20
        self.assert_bad("username", "a" * 21)      # 21 > 20

    def test_filename_length(self):
        self.assert_ok("filename", "a" * 54 + ".txt")   # 主干 54 字符
        self.assert_bad("filename", "a" * 55 + ".txt")  # 主干 55 字符

    def test_rel_path_depth(self):
        self.assert_ok("rel_path", "/".join(["a"] * 5))
        self.assert_bad("rel_path", "/".join(["a"] * 6))

    def test_integer_range(self):
        self.assert_ok("integer", "9223372036854775807")
        self.assert_bad("integer", "9223372036854775808")
        self.assert_ok("integer", "-9223372036854775808")
        self.assert_bad("integer", "-9223372036854775809")

    def test_json_limits(self):
        self.assert_ok("json", "[" * 7 + "1" + "]" * 7)        # 深度 8
        self.assert_bad("json", "[" * 8 + "1" + "]" * 8)       # 深度 9
        self.assert_ok("json", "[" + ",".join(["1"] * 999) + "]")   # 1000 节点
        self.assert_bad("json", "[" + ",".join(["1"] * 1000) + "]")  # 1001 节点
        self.assert_ok("json", '{"k":"' + "v" * 256 + '"}')
        self.assert_bad("json", '{"k":"' + "v" * 257 + '"}')

    def test_empty_and_raw_limit(self):
        for kind in ("username", "slug", "filename", "rel_path", "email",
                     "integer", "json"):
            self.assert_bad(kind, "")
        self.assert_bad("username", "a" * (RAW_INPUT_LIMIT + 1))

    def test_non_string_rejected(self):
        for bad in (None, 123, b"abc", ["a"], {"a": 1}):
            with self.subTest(bad=bad):
                self.assert_bad("username", bad)


class TestBypassCorpus(unittest.TestCase):
    """绕过语料：应拒绝的全部拒绝，等价写法按规范形式接受。"""

    def test_corpus(self):
        for category, kind, desc, payload, expected in CORPUS:
            with self.subTest(category=category, desc=desc):
                if expected == reject:
                    with self.assertRaises(ValidationError):
                        validate(kind, payload)
                else:
                    self.assertEqual(validate(kind, payload), expected[1])


class TestNormalize(unittest.TestCase):
    def test_multi_layer_decoding(self):
        self.assertEqual(normalize("%252e%252e%252f"), "../")
        self.assertEqual(normalize("&amp;#46;"), ".")
        self.assertEqual(normalize("%26%2346%3B"), ".")

    def test_overlong_utf8_rejected(self):
        with self.assertRaises(ValidationError):
            normalize("..%c0%af")

    def test_control_chars_rejected(self):
        for ch in ("\x00", "\x1f", "\x7f", "\x85", "\x9f"):
            with self.subTest(ch=repr(ch)):
                with self.assertRaises(ValidationError):
                    normalize("a" + ch)

    def test_nfkc(self):
        self.assertEqual(normalize("\uff21\uff02"), 'A"')  # 全角 A、全角引号


if __name__ == "__main__":
    unittest.main()
