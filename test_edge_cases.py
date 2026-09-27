#!/usr/bin/env python3
"""Deterministic edge-case tests for the substring matching algorithms."""

from string_match import (
    kmp_find_all,
    naive_find_all,
    prefix_function,
    z_find_all,
    z_function,
)


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")


def test_empty_pattern():
    check("naive empty/empty", naive_find_all(b"", b""), [])
    check("kmp empty/empty", kmp_find_all(b"", b""), [])
    check("z empty/empty", z_find_all(b"", b""), [])
    check("kmp empty/nonempty", kmp_find_all(b"abc", b""), [])
    check("z empty/nonempty", z_find_all("abc", ""), [])


def test_pattern_longer_than_text():
    check("kmp longer bytes", kmp_find_all(b"ab", b"abc"), [])
    check("z longer text", z_find_all("ab", "abc"), [])
    check("naive equal length", naive_find_all(b"abc", b"abd"), [])


def test_binary_bytes():
    text = b"\x00\x01\xff\x00\x00\x01\xff\x00"
    pattern = b"\x00\x01\xff\x00"
    expected = [0, 4]
    check("kmp binary", kmp_find_all(text, pattern), expected)
    check("z binary", z_find_all(text, pattern), expected)
    check("naive binary", naive_find_all(text, pattern), expected)


def test_overlaps():
    check("kmp aaa", kmp_find_all(b"aaaaa", b"aa"), [0, 1, 2, 3])
    check("z aba", z_find_all("abababa", "aba"), [0, 2, 4])
    check("kmp period", kmp_find_all(b"abcabcabc", b"abcabc"), [0, 3])


def test_equal_and_single():
    check("kmp equal", kmp_find_all(b"abc", b"abc"), [0])
    check("z equal", z_find_all("abc", "abc"), [0])
    check("kmp single", kmp_find_all(b"ababa", b"a"), [0, 2, 4])
    check("z single binary", z_find_all(b"\xff\xff", b"\xff"), [0, 1])


def test_all_equal_periodic_arrays():
    check("prefix aaaa", prefix_function("aaaa"), [0, 1, 2, 3])
    check("z aaaa", z_function("aaaa"), [0, 3, 2, 1])
    check("prefix abab", prefix_function("abab"), [0, 0, 1, 2])
    check("z abab", z_function("abab"), [0, 0, 2, 0])

    for algorithm in (naive_find_all, kmp_find_all, z_find_all):
        check(algorithm.__name__, algorithm(b"a" * 100, b"a" * 4), list(range(97)))
        check(
            algorithm.__name__,
            algorithm(b"ab" * 50, b"ababab"),
            list(range(0, 96, 2)),
        )


def main():
    tests = [
        test_empty_pattern,
        test_pattern_longer_than_text,
        test_binary_bytes,
        test_overlaps,
        test_equal_and_single,
        test_all_equal_periodic_arrays,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} edge-case groups passed")


if __name__ == "__main__":
    main()
