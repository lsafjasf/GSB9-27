#!/usr/bin/env python3
"""Timing comparison on inputs that make naive matching quadratic.

The pattern is ``a...ab`` and the text is almost entirely ``a``.  At every
text position the naive algorithm compares m-1 equal characters before the
final mismatch, giving Theta(n*m) character comparisons.  With m = n/2 this
is quadratic.  KMP and the Z scan amortize failed comparisons and remain
linear.
"""

import argparse
import statistics
import time

from string_match import kmp_find_all, naive_find_all, z_find_all


ALGORITHMS = (
    ("naive O(n*m)", naive_find_all),
    ("KMP prefix", kmp_find_all),
    ("Z scan", z_find_all),
)


def make_case(size):
    text = b"a" * size + b"b"
    pattern = b"a" * (size // 2) + b"b"
    return text, pattern


def measure(function, text, pattern, repeats):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        result = function(text, pattern)
        samples.append(time.perf_counter() - start)
    return result, statistics.median(samples)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=(2000, 4000, 8000, 16000),
        help="text lengths to benchmark",
    )
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    print("Degenerate case: text=a^(n)b, pattern=a^(n/2)b")
    print("All algorithms must return the single match at ceil(n/2).")
    print()
    print(
        f"{'n':>7} {'matches':>8} "
        f"{'naive (s)':>12} {'KMP (s)':>12} {'Z (s)':>12} "
        f"{'naive/KMP':>10}"
    )

    baseline = None
    for size in args.sizes:
        text, pattern = make_case(size)
        row = {"matches": [(size + 1) - len(pattern)]}

        for label, function in ALGORITHMS:
            result, elapsed = measure(function, text, pattern, args.repeats)
            assert result == row["matches"], (label, result, row["matches"])
            row[label] = elapsed

        if baseline is None:
            ratio_note = "-"
            baseline = (
                size,
                row["naive O(n*m)"],
                row["KMP prefix"],
                row["Z scan"],
            )
        else:
            size_ratio = size / baseline[0]
            naive_growth = row["naive O(n*m)"] / baseline[1]
            kmp_growth = row["KMP prefix"] / baseline[2]
            z_growth = row["Z scan"] / baseline[3]
            ratio_note = (
                f"x{size_ratio:g}: naive x{naive_growth:.1f}, "
                f"KMP x{kmp_growth:.1f}, Z x{z_growth:.1f}"
            )

        print(
            f"{size:7d} {len(row['matches']):8d} "
            f"{row['naive O(n*m)']:12.6f} "
            f"{row['KMP prefix']:12.6f} "
            f"{row['Z scan']:12.6f} "
            f"{row['naive O(n*m)'] / row['KMP prefix']:10.1f}"
        )
        print(f"         growth from n={baseline[0]}: {ratio_note}")

    print()
    print(
        "Interpretation: doubling n should make naive time grow about 4x, "
        "while KMP/Z grow about 2x apart from fixed overhead."
    )


if __name__ == "__main__":
    main()
