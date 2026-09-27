"""Timing comparison on degenerate inputs.

Two worst-case families for naive matching:

1. all-equal:   pattern "a"*(n/2) in text "a"*n          -> Theta(n*m) work
2. near-miss:   "a"*(n/2-1)+"b" in ("a"*(n/2)+" ")*...   -> every alignment
                 compares m-1 characters and fails at the last one

The naive algorithm is timed at small sizes only (its quadratic blowup is
already visible there), while KMP/Z are additionally timed at much larger
linear scales.  Every timing run is also checked against the naive result at
the smallest scale to guarantee the fast implementations are not just fast.

Output goes to stdout and, when run with ``--save``, to
BENCHMARK_RESULTS.md.
"""

import argparse
import platform
import sys
import time

from linmatch import find_all_kmp, find_all_naive, find_all_z


def timed(fn, pattern, text, repeat):
    best = float("inf")
    result = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn(pattern, text)
        best = min(best, time.perf_counter() - start)
    return result, best


def all_equal_case(size):
    half = size // 2
    return "a" * half, "a" * size


def near_miss_case(size):
    half = size // 2
    return "a" * (half - 1) + "b", ("a" * half + " ") * 2


def ratios(values):
    out = []
    for i, v in enumerate(values):
        out.append(1.0 if i == 0 or values[i - 1] == 0 else v / values[i - 1])
    return out


def run():
    lines = []
    emit = lines.append

    emit("# Degenerate-input timing comparison")
    emit("")
    emit(f"- Python: {platform.python_version()} ({platform.python_implementation()})")
    emit(f"- Platform: {platform.platform()}")
    emit("- Times are best-of repeats, seconds.")
    emit("- Doubling input size: linear work grows ~2x, quadratic work ~4x.")
    emit("")

    # Correctness gate at the smallest degenerate scale.
    for builder, name in ((all_equal_case, "all-equal"), (near_miss_case, "near-miss")):
        p, t = builder(2000)
        ref = find_all_naive(p, t)
        assert find_all_kmp(p, t) == ref, f"KMP mismatch on {name}"
        assert find_all_z(p, t) == ref, f"Z mismatch on {name}"

    small_sizes = (5000, 10000, 20000)
    large_sizes = (200000, 400000, 800000)

    for builder, title in (
        (all_equal_case, "Worst case A: all-equal (pattern length = n/2)"),
        (near_miss_case, "Worst case B: near-miss periodic (mismatch at last char)"),
    ):
        emit(f"## {title}")
        emit("")
        emit("Small scale — all three algorithms (naive runs once):")
        emit("")
        emit("| n | naive (s) | KMP (s) | Z (s) | naive x2 ratio | KMP x2 ratio | Z x2 ratio |")
        emit("|---|-----------|---------|-------|----------------|--------------|------------|")

        naive_times, kmp_times, z_times = [], [], []
        for size in small_sizes:
            pattern, text = builder(size)
            expected, naive_s = timed(find_all_naive, pattern, text, 1)
            kmp_result, kmp_s = timed(find_all_kmp, pattern, text, 3)
            z_result, z_s = timed(find_all_z, pattern, text, 3)
            assert kmp_result == expected
            assert z_result == expected
            naive_times.append(naive_s)
            kmp_times.append(kmp_s)
            z_times.append(z_s)

        rn, rk, rz = ratios(naive_times), ratios(kmp_times), ratios(z_times)
        for i, size in enumerate(small_sizes):
            emit(
                f"| {size} | {naive_times[i]:.4f} | {kmp_times[i]:.5f} | "
                f"{z_times[i]:.5f} | {rn[i]:.2f}x | {rk[i]:.2f}x | {rz[i]:.2f}x |"
            )
        emit("")

        if builder is all_equal_case:
            emit("Large scale — linear algorithms only (naive would take minutes):")
            emit("")
            emit("| n | KMP (s) | Z (s) | KMP x2 ratio | Z x2 ratio |")
            emit("|---|---------|-------|--------------|------------|")
            kmp_big, z_big = [], []
            for size in large_sizes:
                pattern, text = builder(size)
                expected = list(range(size - len(pattern) + 1))
                kmp_result, kmp_s = timed(find_all_kmp, pattern, text, 3)
                z_result, z_s = timed(find_all_z, pattern, text, 3)
                assert kmp_result == expected
                assert z_result == expected
                kmp_big.append(kmp_s)
                z_big.append(z_s)
            rk, rz = ratios(kmp_big), ratios(z_big)
            for i, size in enumerate(large_sizes):
                emit(
                    f"| {size} | {kmp_big[i]:.4f} | {z_big[i]:.4f} | "
                    f"{rk[i]:.2f}x | {rz[i]:.2f}x |"
                )
            emit("")

    emit("Reading: naive ratios stay near 4x (quadratic), while KMP/Z stay")
    emit("near 2x (linear) and handle inputs 40x larger in much less wall time.")
    emit("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true", help="write BENCHMARK_RESULTS.md")
    args = parser.parse_args(argv)
    report = run()
    print(report)
    if args.save:
        with open("BENCHMARK_RESULTS.md", "w", encoding="utf-8") as fh:
            fh.write(report + "\n")


if __name__ == "__main__":
    sys.exit(main())
