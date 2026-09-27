"""Correctness tests: FFT convolution vs naive convolution.

Run:  python3 test_fft.py
Covers: round-trip FFT/IFFT, all-zero, single-point, non-power-of-2
lengths, long sequences, and inputs with extreme magnitude differences
(underflow/overflow guard). Prints an error-vs-length table.
"""

import math
import random
import sys

sys.path.insert(0, "src")
from fft_lib import fft, ifft, fft_convolve, naive_convolve

TOL = 1e-6  # allowed max relative error


def rel_err(got, want):
    num = max((abs(g - w) for g, w in zip(got, want)), default=0.0)
    den = max(1.0, max((abs(w) for w in want), default=0.0))
    return num / den


def check(name, a, b, tol=TOL):
    got = fft_convolve(a, b)
    want = naive_convolve(a, b)
    assert len(got) == len(want), f"{name}: length {len(got)} != {len(want)}"
    e = rel_err(got, want)
    status = "OK " if e <= tol else "FAIL"
    print(f"[{status}] {name:<42} len={len(got):>6}  rel_err={e:.3e}")
    assert e <= tol, f"{name}: rel_err {e:.3e} > {tol}"
    return e


def main():
    rng = random.Random(20260927)

    # 1. FFT/IFFT round trip, incl. non-power-of-2 length (zero-padding path)
    x = [rng.uniform(-1, 1) for _ in range(1000)]
    back = ifft(fft(x))
    e = max(abs(back[i] - x[i]) for i in range(1000))
    print(f"[{'OK ' if e < 1e-9 else 'FAIL'}] fft->ifft round trip (n=1000, padded to 1024)  max_err={e:.3e}")
    assert e < 1e-9

    # 2. Edge cases
    check("all-zero x all-zero", [0.0] * 500, [0.0] * 300)
    check("single point x single point", [3.5], [-2.0])
    check("single point x long", [2.0], [rng.uniform(-1, 1) for _ in range(999)])
    check("impulse x sequence (identity)", [0, 0, 1.0, 0], [rng.uniform(-5, 5) for _ in range(50)])

    # 3. Extreme magnitudes: must not underflow to all-zero, must not overflow
    check("tiny values 1e-200 (underflow guard)",
          [rng.uniform(-1, 1) * 1e-200 for _ in range(64)],
          [rng.uniform(-1, 1) * 1e-150 for _ in range(64)])
    check("huge values 1e150 (overflow guard)",
          [rng.uniform(-1, 1) * 1e150 for _ in range(64)],
          [rng.uniform(-1, 1) * 1e150 for _ in range(64)])
    check("mixed magnitude 1e150 vs 1e-150",
          [rng.uniform(-1, 1) * 1e150 for _ in range(33)],
          [rng.uniform(-1, 1) * 1e-150 for _ in range(33)])
    mixed = [rng.uniform(-1, 1) * (10.0 ** rng.randint(-100, 100)) for _ in range(128)]
    check("per-sample magnitude spread 1e-100..1e100", mixed, [rng.uniform(-1, 1) for _ in range(128)])

    # 4. Long sequence (non-power-of-2 length)
    n = 100_001
    check("long sequence n=100001 (non-pow2)",
          [rng.uniform(-1, 1) for _ in range(n)],
          [rng.uniform(-1, 1) for _ in range(777)],
          tol=1e-8)

    # 5. Error vs length table (non-power-of-2 lengths to exercise padding)
    print("\nerror vs length (random uniform [-1,1], equal lengths):")
    print(f"{'n':>8} {'fft_len':>8} {'max_rel_err':>12}")
    for n in [8, 31, 100, 333, 1000, 3000, 10000]:
        a = [rng.uniform(-1, 1) for _ in range(n)]
        b = [rng.uniform(-1, 1) for _ in range(n)]
        got = fft_convolve(a, b)
        want = naive_convolve(a, b)
        e = rel_err(got, want)
        from fft_lib import next_pow2
        print(f"{n:>8} {next_pow2(2 * n - 1):>8} {e:>12.3e}")
        assert e < 1e-8, f"n={n}: error too large"

    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
