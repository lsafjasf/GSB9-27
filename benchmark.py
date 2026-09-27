"""Wall-clock comparison: monotonic-deque structure vs brute force.

Run:  python3 benchmark.py
Both implementations process the same deterministic push stream and answer
min+max after every insertion.  Brute force rescans the whole window each
time (O(n*w)); the deque version is O(n) total.
"""

import random
import time

from brute_force import BruteForceWindowMinMax
from sliding_window_extrema import SlidingWindowMinMax


def _make_stream(n, seed=20260927):
    rng = random.Random(seed)
    return [rng.randint(0, 1_000_000) for _ in range(n)]


def bench_deque(stream, window_size):
    sw = SlidingWindowMinMax(window_size)
    lo = hi = 0
    for value in stream:
        sw.push(value)
        lo, hi = sw.get_min_max()
    return lo, hi


def bench_brute(stream, window_size):
    bf = BruteForceWindowMinMax(window_size)
    lo = hi = 0
    for value in stream:
        bf.push(value)
        lo, hi = bf.get_min_max()
    return lo, hi


def bench_grow_resize(target_window):
    """Cost of one resize that grows the window from 0 to target_window."""
    sw = SlidingWindowMinMax(0)
    for _ in range(target_window):
        sw.push(0)
    start = time.perf_counter()
    sw.resize(target_window)
    return time.perf_counter() - start


def timeit(fn, *args, repeat=3):
    best = float("inf")
    result = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn(*args)
        best = min(best, time.perf_counter() - start)
    return best, result


def main():
    cases = [
        # (window_size, n_pushed)
        (1, 200_000),
        (100, 200_000),
        (1_000, 50_000),
        (10_000, 20_000),
    ]

    print(f"{'window':>8} {'pushes':>9} {'deque (s)':>12} "
          f"{'brute (s)':>12} {'speedup':>10}")
    print("-" * 66)
    for window_size, n in cases:
        stream = _make_stream(n)

        deque_time, deque_result = timeit(bench_deque, stream, window_size)
        brute_time, brute_result = timeit(bench_brute, stream, window_size)
        assert deque_result == brute_result, "implementations disagree!"

        speedup = brute_time / deque_time if deque_time else float("inf")
        print(f"{window_size:>8} {n:>9} {deque_time:>12.4f} "
              f"{brute_time:>12.4f} {speedup:>9.1f}x")

    print()
    print("Dynamic resize cost (growing 0 -> w over w retained values):")
    print(f"{'new_window':>12} {'resize (s)':>12} {'s/element (us)':>16}")
    for target_window in (1_000, 10_000, 100_000):
        elapsed = bench_grow_resize(target_window)
        print(f"{target_window:>12} {elapsed:>12.5f} "
              f"{elapsed / target_window * 1e6:>16.3f}")


if __name__ == "__main__":
    main()
