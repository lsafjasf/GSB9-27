"""Edge-case and behavior tests for linmatch (run with unittest)."""

import unittest

from linmatch import (
    find_all_kmp,
    find_all_naive,
    find_all_z,
    prefix_function,
    z_function,
)


ALGORITHMS = (find_all_kmp, find_all_z, find_all_naive)


class CoreFunctionsTest(unittest.TestCase):
    def test_prefix_function_known_values(self):
        self.assertEqual(prefix_function(""), [])
        self.assertEqual(prefix_function("a"), [0])
        self.assertEqual(prefix_function("aaa"), [0, 1, 2])
        self.assertEqual(prefix_function("ababab"), [0, 0, 1, 2, 3, 4])
        self.assertEqual(prefix_function("abcabcd"), [0, 0, 0, 1, 2, 3, 0])

    def test_z_function_known_values(self):
        self.assertEqual(z_function(""), [])
        self.assertEqual(z_function("a"), [0])
        self.assertEqual(z_function("aaaa"), [0, 3, 2, 1])
        self.assertEqual(z_function("ababa"), [0, 0, 3, 0, 1])
        self.assertEqual(z_function("abcababc"), [0, 0, 0, 2, 0, 3, 0, 0])


class MatchTest(unittest.TestCase):
    def assert_all_equal(self, pattern, text, expected):
        for algo in ALGORITHMS:
            with self.subTest(algo=algo.__name__):
                self.assertEqual(algo(pattern, text), expected)

    def test_basic_and_non_match(self):
        self.assert_all_equal("abab", "abababab", [0, 2, 4])
        self.assert_all_equal("abc", "abxabc", [3])
        self.assert_all_equal("xyz", "abababab", [])

    def test_overlapping_matches(self):
        # "aa" appears at every offset except the last.
        self.assert_all_equal("aa", "aaaaa", [0, 1, 2, 3])
        self.assert_all_equal("aaa", "aaaaa", [0, 1, 2])
        self.assert_all_equal("aba", "abababa", [0, 2, 4])

    def test_empty_pattern_returns_empty(self):
        # Deterministic choice: empty pattern never matches (documented).
        self.assert_all_equal("", "", [])
        self.assert_all_equal("", "abc", [])

    def test_empty_text_nonempty_pattern(self):
        self.assert_all_equal("a", "", [])

    def test_pattern_longer_than_text(self):
        self.assert_all_equal("abcd", "abc", [])
        self.assert_all_equal("aaaa", "aaa", [])

    def test_equal_length_pattern_and_text(self):
        self.assert_all_equal("abc", "abc", [0])
        self.assert_all_equal("abc", "abd", [])

    def test_single_element(self):
        self.assert_all_equal("a", "banana", [1, 3, 5])

    def test_binary_bytes(self):
        self.assert_all_equal(b"\x00\x01", b"\x00\x01\x00\x01", [0, 2])
        self.assert_all_equal(b"\xff", b"\x00\xff\xfe\xff", [1, 3])
        self.assert_all_equal(b"abab", bytearray(b"abababab"), [0, 2, 4])
        self.assert_all_equal(b"", b"abc", [])
        self.assert_all_equal(b"abcd", b"abc", [])

    def test_all_same_input(self):
        self.assert_all_equal("a" * 1000, "a" * 1000, list(range(1)))
        self.assert_all_equal("a" * 500, "a" * 1000, list(range(501)))

    def test_periodic_input(self):
        pattern = "ab" * 200
        text = "ab" * 500
        expected = list(range(0, len(text) - len(pattern) + 1, 2))
        self.assert_all_equal(pattern, text, expected)

    def test_almost_matching_periodic_prefix(self):
        # Long cheap matches that fail at the last character.
        pattern = "a" * 99 + "b"
        text = ("a" * 100 + " ") * 10
        self.assert_all_equal(pattern, text, [])

    def test_unicode_code_points(self):
        self.assert_all_equal("码", "源码码", [1, 2])

    def test_positions_are_sorted(self):
        for algo in ALGORITHMS:
            result = algo("ab", "ab" * 100)
            self.assertEqual(result, sorted(result))

    def test_type_errors(self):
        for algo in ALGORITHMS:
            with self.subTest(algo=algo.__name__):
                with self.assertRaises(TypeError):
                    algo("ab", b"ab")
                with self.assertRaises(TypeError):
                    algo(b"ab", "ab")
                with self.assertRaises(TypeError):
                    algo(["a", "b"], "ab")


if __name__ == "__main__":
    unittest.main(verbosity=2)
