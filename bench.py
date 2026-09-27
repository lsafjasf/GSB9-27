"""Larger-scale timing of the DP selector (standard library only).

Usage: python3 bench.py
"""

import random
import time

from iselect import select, _refcounts
from isa import default_isa
from compare import random_dag


def main():
    isa = default_isa()
    print("%-9s %-13s %-13s %-9s %-10s" %
          ("nodes", "materialized", "instructions", "cost", "time(ms)"))
    for n in [1_000, 10_000, 100_000, 300_000]:
        rng = random.Random(42)
        roots = random_dag(rng, n, extra_roots=max(1, n // 2000))
        times = []
        for _ in range(3):
            t0 = time.perf_counter()
            sel = select(roots, isa)
            times.append((time.perf_counter() - t0) * 1000)
        n_mat = sum(1 for c in _refcounts(roots).values() if c > 1) + 0
        print("%-9d %-13d %-13d %-9d %-10.2f" %
              (n, n_mat, len(sel.steps), sel.total_cost, min(times)))
    print()
    print("note: times are best-of-3 wall clock, Python 3, stdlib only")


if __name__ == "__main__":
    main()
