"""自测 + 与朴素实现对拍。

运行：python3 test_suffix_array.py   （或 python3 -m unittest -v）
"""

import random
import unittest

from suffix_array import SuffixArray, build_suffix_array, build_lcp_array
from naive import (
    naive_suffix_array,
    naive_lcp_array,
    naive_lcp_of_suffixes,
    naive_longest_repeated_substring,
)


def check_against_naive(tc, s: bytes):
    """对单条用例做全量一致性校验。"""
    n = len(s)
    sa = build_suffix_array(s)
    # 后缀数组：与朴素排序逐项一致
    tc.assertEqual(sa, naive_suffix_array(s), f"SA mismatch, s={s!r}")
    # SA 是 0..n-1 的排列
    tc.assertEqual(sorted(sa), list(range(n)))
    # LCP 数组：与朴素定义逐项一致
    lcp = build_lcp_array(s, sa)
    tc.assertEqual(lcp, naive_lcp_array(s, sa), f"LCP mismatch, s={s!r}")
    # LCP 数组满足定义：lcp[i] = LCP(sa[i-1], sa[i])
    for idx in range(1, n):
        tc.assertEqual(
            lcp[idx],
            naive_lcp_of_suffixes(s, sa[idx - 1], sa[idx]),
            f"LCP def mismatch at {idx}, s={s!r}",
        )
    # 任意两后缀 LCP 查询 vs 暴力
    obj = SuffixArray(s)
    rng = random.Random(hash(s) & 0xFFFFFFFF)
    for _ in range(min(4 * n + 4, 60)):
        i = rng.randrange(n) if n else 0
        j = rng.randrange(n) if n else 0
        if n:
            tc.assertEqual(
                obj.lcp_of_suffixes(i, j),
                naive_lcp_of_suffixes(s, i, j),
                f"lcp_of_suffixes({i},{j}) mismatch, s={s!r}",
            )
    # 最长重复子串：长度与暴力一致，且返回的子串确实出现至少两次
    length, pos = obj.longest_repeated_substring()
    naive_len, _ = naive_longest_repeated_substring(s)
    tc.assertEqual(length, naive_len, f"LRS length mismatch, s={s!r}")
    if length:
        sub = s[pos:pos + length]
        first = s.find(sub)
        tc.assertGreaterEqual(first, 0)
        tc.assertGreater(s.find(sub, first + 1), -1, f"LRS not repeated, s={s!r}")


class EdgeCaseTests(unittest.TestCase):
    def test_empty(self):
        s = b""
        self.assertEqual(build_suffix_array(s), [])
        self.assertEqual(build_lcp_array(s, []), [])
        obj = SuffixArray(s)
        self.assertEqual(obj.longest_repeated_substring(), (0, 0))
        with self.assertRaises(IndexError):
            obj.lcp_of_suffixes(0, 0)

    def test_single_byte(self):
        for b in (0x00, 0x41, 0xFF):
            s = bytes([b])
            self.assertEqual(build_suffix_array(s), [0])
            self.assertEqual(build_lcp_array(s, [0]), [0])
            obj = SuffixArray(s)
            self.assertEqual(obj.longest_repeated_substring(), (0, 0))
            self.assertEqual(obj.lcp_of_suffixes(0, 0), 1)

    def test_all_same_byte(self):
        for n in (1, 2, 3, 5, 17, 100):
            for b in (0x00, 0x61, 0xFF):
                s = bytes([b]) * n
                # 全同字节串的 SA 必为 [n-1, n-2, ..., 0]
                self.assertEqual(build_suffix_array(s), list(range(n - 1, -1, -1)))
                obj = SuffixArray(s)
                self.assertEqual(obj.longest_repeated_substring(), (n - 1, 0) if n >= 2 else (0, 0))
                if n >= 2:
                    self.assertEqual(obj.lcp_of_suffixes(0, 1), n - 1)

    def test_all_byte_values(self):
        s = bytes(range(256))  # 含 \x00 与 \xff，互异字节
        check_against_naive(self, s)
        obj = SuffixArray(s)
        self.assertEqual(obj.longest_repeated_substring(), (0, 0))

    def test_binary_with_nulls_and_high_bytes(self):
        cases = [
            b"\x00\xff\x00\x00\xff\x00",
            b"\xff\xff\x00\xff\xff\x00",
            b"\x00" * 50 + b"\xff" + b"\x00" * 50,
            b"ab\x00ab\x00ab",
            bytes([255, 0, 255, 0, 255, 0]),
        ]
        for s in cases:
            check_against_naive(self, s)

    def test_known_example(self):
        s = b"banana"
        self.assertEqual(build_suffix_array(s), [5, 3, 1, 0, 4, 2])
        self.assertEqual(build_lcp_array(s, build_suffix_array(s)), [0, 1, 3, 0, 0, 2])
        obj = SuffixArray(s)
        length, pos = obj.longest_repeated_substring()
        self.assertEqual((length, s[pos:pos + length]), (3, b"ana"))
        self.assertEqual(obj.lcp_of_suffixes(1, 3), 3)  # "anana" vs "ana"

    def test_periodic_strings(self):
        for period in (b"a", b"ab", b"abc", b"\x00\x01"):
            for reps in (2, 3, 10):
                check_against_naive(self, period * reps)

    def test_accepts_bytearray(self):
        obj = SuffixArray(bytearray(b"mississippi"))
        self.assertEqual(obj.longest_repeated_substring()[0], 4)  # "issi"


class RandomCrossCheckTests(unittest.TestCase):
    """随机字节串对拍：SA 与 LCP 必须与朴素实现逐项一致。"""

    def test_random_small_exhaustive_alphabets(self):
        rng = random.Random(20260928)
        trials = 0
        for _ in range(400):
            n = rng.randrange(0, 130)
            alphabet = rng.choice([1, 2, 3, 4, 5, 16, 256])
            s = bytes(rng.randrange(alphabet) for _ in range(n))
            check_against_naive(self, s)
            trials += 1
        print(f"\n[对拍] 随机用例 {trials} 组全部一致")

    def test_random_structured(self):
        # 高重复结构：随机小块反复拼接，制造大量重复子串
        rng = random.Random(987654321)
        for _ in range(120):
            unit = bytes(rng.randrange(4) for _ in range(rng.randrange(1, 8)))
            s = (unit * rng.randrange(1, 25))[: rng.randrange(0, 150)]
            check_against_naive(self, s)

    def test_random_medium(self):
        rng = random.Random(13579)
        for _ in range(15):
            n = rng.randrange(500, 1500)
            alphabet = rng.choice([2, 4, 256])
            s = bytes(rng.randrange(alphabet) for _ in range(n))
            check_against_naive(self, s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
