"""Timing comparison: naive O(n^2) convolution vs FFT convolution.

Run:  python3 bench.py
"""

import random
import sys
import time

sys.path.insert(0, "src")
from fft_lib import fft_convolve, naive_convolve


def timeit(fn, a, b, repeats):
    best = float("inf")
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn(a, b)
        best = min(best, time.perf_counter() - t0)
    return best


def main():
    rng = random.Random(7)
    print(f"{'n':>8} {'naive (s)':>12} {'fft (s)':>12} {'speedup':>9}")
    crossover = None
    naive_cost = None  # measured seconds per n^2
    for n in [16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768,
              65536, 131072, 262144, 524288, 1048576]:
        a = [rng.uniform(-1, 1) for _ in range(n)]
        b = [rng.uniform(-1, 1) for _ in range(n)]
        reps_fft = max(1, min(50, int(5e5 / n)))
        if n <= 32768:
            reps_naive = max(1, min(20, int(2e6 / (n * n))))
            t_naive = timeit(naive_convolve, a, b, reps_naive)
            naive_cost = t_naive / (n * n)
            naive_note = ""
        else:
            t_naive = naive_cost * n * n  # extrapolated O(n^2)
            naive_note = " (extrap.)"
        t_fft = timeit(fft_convolve, a, b, reps_fft)
        speedup = t_naive / t_fft
        if crossover is None and t_fft < t_naive:
            crossover = n
        print(f"{n:>8} {t_naive:>12.6f} {t_fft:>12.6f} {speedup:>8.2f}x{naive_note}")
    print(f"\nFFT convolution becomes faster at about n = {crossover} "
          f"(equal-length inputs, this machine).")


if __name__ == "__main__":
    main()
