"""性能实测：倍增法（O(n log n)） vs 朴素后缀排序（O(n^2 log n)）。

运行：python3 bench.py
"""

import random
import time
import tracemalloc

from suffix_array import build_suffix_array, build_lcp_array
from naive import naive_suffix_array


def timed(fn, *args, repeat=1):
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn(*args)
        best = min(best, time.perf_counter() - t0)
    return best


def peak_memory(fn, *args):
    tracemalloc.start()
    fn(*args)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak


def fmt_row(n, t_naive, t_fast):
    naive = f"{t_naive:>12.3f}" if t_naive is not None else f"{'(过慢,跳过)':>12}"
    ratio = f"{t_naive / t_fast:>7.1f}x" if t_naive is not None else f"{'-':>8}"
    return f"{n:>9} | {naive} | {t_fast:>10.4f} | {ratio}"


def main():
    rng = random.Random(42)
    header = f"{'n':>9} | {'朴素排序(s)':>12} | {'倍增法(s)':>10} | {'加速比':>8}"

    print("== 随机字节串（256 字母表）==")
    print(header)
    print("-" * 50)
    for n in (1000, 4000, 16000, 64000, 256000, 1000000):
        s = rng.randbytes(n)
        t_fast = timed(build_suffix_array, s, repeat=3)
        t_naive = timed(naive_suffix_array, s) if n <= 64000 else None
        print(fmt_row(n, t_naive, t_fast))

    print()
    print("== 高重复文本（全同字节，朴素法最坏情形）==")
    print(header)
    print("-" * 50)
    for n in (1000, 4000, 16000, 64000, 256000, 1000000):
        s = b"a" * n
        t_fast = timed(build_suffix_array, s, repeat=3)
        t_naive = timed(naive_suffix_array, s) if n <= 64000 else None
        print(fmt_row(n, t_naive, t_fast))

    print()
    print("== 峰值内存对比（tracemalloc，n=32000）==")
    s = rng.randbytes(32000)
    m_naive = peak_memory(naive_suffix_array, s)
    m_fast = peak_memory(build_suffix_array, s)
    print(f"朴素排序: {m_naive / 2**20:8.1f} MiB   倍增法: {m_fast / 2**20:8.1f} MiB")

    print()
    print("== LCP 数组（Kasai）耗时，n=1000000 随机字节串 ==")
    s = rng.randbytes(1000000)
    sa = build_suffix_array(s)
    print(f"build_lcp_array: {timed(build_lcp_array, s, sa, repeat=3):.4f}s")


if __name__ == "__main__":
    main()
