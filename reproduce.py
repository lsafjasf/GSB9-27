"""Reproduce the fragmentation issue and verify the fix.

Runs the same long-lived mixed-size workload against the buggy pool and
the fixed pool, printing the fragmentation ratio (1 - largest_free/total_free)
as the allocation sequence grows, then attempts one large allocation that
the buggy pool cannot satisfy despite having plenty of total free memory.

Usage: python3 reproduce.py
"""

import random

from mempool_buggy import BuggyMemoryPool, OutOfMemoryError as BuggyOOM
from mempool import MemoryPool, OutOfMemoryError as FixedOOM

CAPACITY = 1 << 18          # 256 KiB arena
ROUNDS = 3000               # length of the allocation sequence
BIG_REQUEST = 64 << 10      # 64 KiB -- the "large" allocation
SIZE_LO, SIZE_HI = 24, 1024  # random small-object sizes


def run_workload(pool, oom_error, rounds=ROUNDS, seed=42):
    """Mixed-size churn: allocate a few blocks, free ~60% of live ones."""
    rng = random.Random(seed)
    live = []
    history = []
    checkpoints = {rounds // 8, rounds // 4, rounds // 2, rounds - 1}
    for r in range(rounds):
        for _ in range(4):
            try:
                live.append(pool.alloc(rng.randint(SIZE_LO, SIZE_HI)))
            except oom_error:
                pass
        rng.shuffle(live)
        cut = int(len(live) * 0.6)
        for off in live[:cut]:
            pool.free(off)
        del live[:cut]
        if r in checkpoints:
            history.append((r + 1, pool.total_free(),
                            pool.largest_free_block(), pool.fragmentation()))
    return live, history


def report(name, history):
    print("\n[%s] fragmentation vs. allocation-sequence length" % name)
    print("  %-8s %12s %14s %14s" % ("allocs", "total_free", "largest_free",
                                     "fragmentation"))
    for step, total, largest, frag in history:
        print("  %-8d %12d %14d %13.1f%%" % (step, total, largest,
                                             frag * 100))


def try_big_alloc(pool, oom_error, label):
    total = pool.total_free()
    try:
        off = pool.alloc(BIG_REQUEST)
    except oom_error as exc:
        print("  %s: alloc(%d KiB) FAILED  (total free = %d KiB) -> %s"
              % (label, BIG_REQUEST >> 10, total >> 10, exc))
        return False
    print("  %s: alloc(%d KiB) OK at offset %d (total free was %d KiB)"
          % (label, BIG_REQUEST >> 10, off, total >> 10))
    pool.free(off)
    return True


def main():
    buggy = BuggyMemoryPool(CAPACITY)
    fixed = MemoryPool(CAPACITY)

    _, buggy_hist = run_workload(buggy, BuggyOOM)
    _, fixed_hist = run_workload(fixed, FixedOOM)

    report("buggy pool", buggy_hist)
    report("fixed pool", fixed_hist)

    print("\n[large allocation after churn]  request = %d KiB" %
          (BIG_REQUEST >> 10))
    buggy_ok = try_big_alloc(buggy, BuggyOOM, "buggy")
    fixed_ok = try_big_alloc(fixed, FixedOOM, "fixed")

    print("\n[summary]")
    print("  buggy: fragmentation %.1f%%, big alloc %s"
          % (buggy.fragmentation() * 100, "succeeded" if buggy_ok else "FAILED"))
    print("  fixed: fragmentation %.1f%%, big alloc %s"
          % (fixed.fragmentation() * 100, "succeeded" if fixed_ok else "FAILED"))
    fixed.check_invariants()
    print("  fixed pool structural invariants: OK")

    if buggy_ok or not fixed_ok:
        raise SystemExit("unexpected result: reproduction failed")


if __name__ == "__main__":
    main()
