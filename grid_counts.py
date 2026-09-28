"""Count the parameter combinations actually evaluated by `_select` for the
default and fine grids, plus the empirical grid-search wall-clock ratio.

These are the numbers quoted in README.md.
Run: python3 grid_counts.py
"""

import math
import random
import time

import forecast as fc
from forecast import DEFAULT_GRID, FINE_GRID


def count_evaluated(y, season_length, grid):
    """Run `_select` with the three fitters instrumented and return how many
    times each component was evaluated (feasible and infeasible alike)."""
    counts = {"ses": 0, "holt": 0, "hw-add": 0, "hw-mul": 0}
    orig_ses, orig_holt, orig_hw = fc._fit_ses, fc._fit_holt, fc._fit_hw

    def wrap_ses(y, alpha):
        counts["ses"] += 1
        return orig_ses(y, alpha)

    def wrap_holt(y, alpha, beta):
        counts["holt"] += 1
        return orig_holt(y, alpha, beta)

    def wrap_hw(y, m, alpha, beta, gamma, kind):
        counts["hw-" + kind] += 1
        return orig_hw(y, m, alpha, beta, gamma, kind)

    fc._fit_ses, fc._fit_holt, fc._fit_hw = wrap_ses, wrap_holt, wrap_hw
    try:
        fc._select(y, season_length, "auto", grid)
    finally:
        fc._fit_ses, fc._fit_holt, fc._fit_hw = orig_ses, orig_holt, orig_hw
    total = sum(counts.values())
    return counts, total


def theoretical(g):
    """Combinations on the seasonal auto path (n >= 2 * season_length):
    ses g + holt g^2 + two Holt-Winters kinds 2 * g^3."""
    counts = {"ses": g, "holt": g * g, "hw-add": g ** 3, "hw-mul": g ** 3}
    return counts, sum(counts.values())


def main():
    # Positive trend+seasonal series long enough that every component loop
    # in `_select` runs (n=120 >= 2*12).
    rng = random.Random(42)
    y = [60 + 0.4 * t + 10 + 8 * math.sin(2 * math.pi * t / 12)
         + rng.gauss(0, 2) for t in range(120)]

    print("candidate combinations evaluated by _select (seasonal='auto', "
          "n=120, m=12)")
    print(f"{'grid':<16}{'ses':>6}{'holt':>7}{'hw-add':>8}"
          f"{'hw-mul':>8}{'total':>8}")
    rows = {}
    for name, grid in (("default (g=8)", DEFAULT_GRID), ("fine (g=13)", FINE_GRID)):
        counts, total = count_evaluated(y, 12, tuple(grid))
        rows[name] = total
        print(f"{name:<16}{counts['ses']:>6}{counts['holt']:>7}"
              f"{counts['hw-add']:>8}{counts['hw-mul']:>8}{total:>8}")

    c_d, t_d = theoretical(len(DEFAULT_GRID))
    c_f, t_f = theoretical(len(FINE_GRID))
    print()
    print("formula  g + g^2 + 2*g^3:")
    print(f"  default g={len(DEFAULT_GRID)}: {c_d['ses']} + {c_d['holt']} + "
          f"{c_d['hw-add'] + c_d['hw-mul']} = {t_d}")
    print(f"  fine    g={len(FINE_GRID)}: {c_f['ses']} + {c_f['holt']} + "
          f"{c_f['hw-add'] + c_f['hw-mul']} = {t_f}")
    ratio = t_f / t_d
    print(f"  fine/default candidate ratio: {t_f} / {t_d} = {ratio:.2f}x")

    # Empirical wall-clock of the full grid search, best of 3 runs.
    def best_time(grid):
        best = float("inf")
        for _ in range(3):
            t0 = time.perf_counter()
            fc.forecast(y, horizon=8, season_length=12, grid=tuple(grid))
            best = min(best, time.perf_counter() - t0)
        return best

    td = best_time(DEFAULT_GRID)
    tf = best_time(FINE_GRID)
    print()
    print(f"empirical forecast() grid-search wall clock (best of 3):")
    print(f"  default: {td * 1000:.1f} ms")
    print(f"  fine:    {tf * 1000:.1f} ms")
    print(f"  fine/default time ratio: {tf / td:.2f}x")


if __name__ == "__main__":
    main()
