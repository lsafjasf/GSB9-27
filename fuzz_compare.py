"""Differential testing: KMP and Z outputs must equal the naive reference.

Covers random inputs (several alphabets, incl. binary bytes), random empty
patterns, random longer-than-text patterns, and adversarial constructed
inputs (all equal, periodic, near-miss periodic).  Run directly:

    python3 fuzz_compare.py [seed]

Exits non-zero on the first disagreement and prints a reproducible case.
"""

import random
import sys

from linmatch import find_all_kmp, find_all_naive, find_all_z

ALGORITHMS = {
    "kmp": find_all_kmp,
    "z": find_all_z,
}


def check_case(pattern, text, case_name):
    expected = find_all_naive(pattern, text)
    for name, algo in ALGORITHMS.items():
        got = algo(pattern, text)
        if got != expected:
            raise AssertionError(
                f"{case_name}: {name} {got!r} != naive {expected!r}\n"
                f"pattern={pattern!r}\ntext={text!r}"
            )


def random_case(rng, alphabet, text_builder):
    n = rng.randrange(0, 400)
    # Pattern length spans 0 (empty), longer-than-text, and normal sizes.
    m = rng.choice(
        [0, n + 1, n + 5]
        + [rng.randrange(1, max(2, n + 3)) for _ in range(6)]
    )
    text = text_builder(rng, n, alphabet)
    pattern = text_builder(rng, m, alphabet)
    return pattern, text


def build_str(rng, length, alphabet):
    return "".join(rng.choice(alphabet) for _ in range(length))


def build_bytes(rng, length, alphabet):
    return bytes(rng.choice(alphabet) for _ in range(length))


def random_suite(rng):
    cases = 0
    alphabets = ["ab", "abc", "abcd", "ACGT", "0123456789", "汉字码元"]
    for _ in range(4000):
        pattern, text = random_case(rng, rng.choice(alphabets), build_str)
        check_case(pattern, text, f"random str case {cases}")
        cases += 1
    for _ in range(2000):
        pattern, text = random_case(rng, (0, 1, 255, 254), build_bytes)
        check_case(pattern, text, f"random bytes case {cases}")
        cases += 1
    return cases


def constructed_suite():
    cases = 0
    # All-equal inputs at multiple sizes (empty included).
    for m in list(range(0, 40)) + [100, 500]:
        for n in list(range(0, 40)) + [100, 500, 1000]:
            check_case("a" * m, "a" * n, f"all-equal m={m} n={n}")
            check_case(b"\x00" * m, b"\x00" * n, f"all-equal bytes m={m} n={n}")
            cases += 2
    # Periodic inputs: pattern and text share the same / different periods.
    for period in ("ab", "abc", "abcab", "01"):
        for pm in range(1, 8):
            for tm in range(0, 12):
                pattern = (period * pm)[: max(1, pm * len(period))]
                text = (period * tm)[: max(0, tm * len(period))]
                check_case(pattern, text, f"periodic {period}")
                cases += 1
    # Near-miss: long matching prefix, mismatch only at the last position.
    for m in (2, 10, 100):
        for repeats in (1, 5, 20):
            pattern = "a" * (m - 1) + "b"
            text = ("a" * m + " ") * repeats
            check_case(pattern, text, "near-miss periodic")
            cases += 1
    # Pattern longer than text on periodic / identical strings.
    check_case("ab" * 100, "ab" * 50, "periodic longer pattern")
    check_case(b"\x01" * 300, b"\x01" * 100, "identical longer bytes")
    cases += 2
    return cases


def main(argv):
    seed = int(argv[1]) if len(argv) > 1 else 20260927
    rng = random.Random(seed)
    count = random_suite(rng)
    count += constructed_suite()
    print(f"fuzz ok: {count} cases compared against naive (seed={seed})")


if __name__ == "__main__":
    main(sys.argv)
