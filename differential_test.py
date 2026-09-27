#!/usr/bin/env python3
"""Compare KMP and Z-function matching against the naive implementation."""

import random

from string_match import kmp_find_all, naive_find_all, z_find_all


ALGORITHMS = (kmp_find_all, z_find_all)


def assert_same(text, pattern, case_name):
    expected = naive_find_all(text, pattern)
    for algorithm in ALGORITHMS:
        actual = algorithm(text, pattern)
        if actual != expected:
            raise AssertionError(
                f"{case_name} / {algorithm.__name__}: "
                f"expected {expected!r}, got {actual!r}"
            )


def random_bytes(rng, text_length, pattern_length, alphabet_size=4):
    return bytes(rng.randrange(alphabet_size) for _ in range(text_length)), bytes(
        rng.randrange(alphabet_size) for _ in range(pattern_length)
    )


def random_string(rng, text_length, pattern_length, alphabet="abcd"):
    text = "".join(rng.choice(alphabet) for _ in range(text_length))
    pattern = "".join(rng.choice(alphabet) for _ in range(pattern_length))
    return text, pattern


def run_random_cases():
    rng = random.Random(20260927)
    count = 0

    for _ in range(3000):
        text_length = rng.randrange(0, 80)
        pattern_length = rng.randrange(0, 25)
        if rng.randrange(2):
            text, pattern = random_bytes(rng, text_length, pattern_length)
        else:
            text, pattern = random_string(rng, text_length, pattern_length)
        assert_same(text, pattern, "random")
        count += 1

    for _ in range(500):
        text_length = rng.randrange(1, 200)
        pattern_length = rng.randrange(1, 20)
        text, pattern = random_bytes(rng, text_length, pattern_length, 2)
        assert_same(text, pattern, "binary-alphabet random")
        count += 1

    return count


def run_constructed_cases():
    cases = [
        (b"", b""),
        (b"a", b""),
        (b"", b"a"),
        (b"a", b"aa"),
        (b"a" * 200, b"a"),
        (b"a" * 200, b"a" * 7),
        (b"a" * 199 + b"b", b"a" * 100 + b"b"),
        (b"ab" * 100, b"abababab"),
        (b"abc" * 100, b"abcabc"),
        (b"abc" * 100, b"abcabd"),
        (b"aaaaab" * 40, b"aaaab"),
        (b"\x00" * 100 + b"\x01", b"\x00" * 50 + b"\x01"),
        (b"\xff" * 100, b"\xff" * 10),
        ("a" * 200, "a" * 8),
        ("ababababab", "abab"),
    ]

    count = 0
    for index, (text, pattern) in enumerate(cases, 1):
        assert_same(text, pattern, f"constructed #{index}")
        count += 1

    for size in range(1, 40):
        text = b"a" * size
        for pattern_size in range(1, size + 2):
            assert_same(text, b"a" * pattern_size, f"all-equal n={size}")
            count += 1

        period = b"ab"
        text = period * size
        for pattern in (period, period * 2, period * 3 + b"a", b"aba"):
            assert_same(text, pattern, f"periodic n={size}")
            count += 1

    return count


def main():
    random_count = run_random_cases()
    constructed_count = run_constructed_cases()
    print(
        f"PASS {random_count} random cases and "
        f"{constructed_count} constructed/boundary comparisons"
    )


if __name__ == "__main__":
    main()
