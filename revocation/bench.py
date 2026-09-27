"""Benchmark: 1,000,000 check() calls. Run: python3 bench.py"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from revocation import RevocationList, Verdict

N = 1_000_000
BASE = 1_000_000.0


def main():
    rl = RevocationList(now=lambda: BASE + 500)
    # Half the queries hit revoked tokens, half miss.
    rl.revoke_many((f"revoked-{i}", BASE + 1000) for i in range(50_000))

    start = time.perf_counter()
    counts = {Verdict.REVOKED: 0, Verdict.NOT_REVOKED: 0, Verdict.UNKNOWN: 0}
    for i in range(N):
        if i % 2 == 0:
            v = rl.check(f"revoked-{i % 50_000}", exp=BASE + 1000)
        else:
            v = rl.check(f"clean-{i}", exp=BASE + 1000)
        counts[v] += 1
    elapsed = time.perf_counter() - start

    print(f"queries        : {N:,}")
    print(f"total time     : {elapsed:.3f} s")
    print(f"per query      : {elapsed / N * 1e6:.3f} us")
    print(f"throughput     : {N / elapsed:,.0f} qps")
    print(f"verdict counts : { {k.value: v for k, v in counts.items()} }")
    print(f"store size     : {rl.memory_stats()}")


if __name__ == "__main__":
    main()
