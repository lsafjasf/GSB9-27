"""Elapsed-time distribution: legacy (before) vs fixed (after).

Runs randomized plans of three shapes (serial / retry / parallel) through
both executors with an injected FakeClock and prints min/p50/p95/max of
the total elapsed time plus how many runs violated the budget.
Deterministic (fixed seed).
"""
import random
import statistics

from budget_timeout import FakeClock, Par, Retry, Seq, Work, run
from legacy_timeout import run as legacy_run

BUDGET = 1.0
TRIALS = 2000


def make_plans(rng):
    serial = Seq("p", [Work(f"s{i}", rng.uniform(0.2, 0.9)) for i in range(5)])
    retry = Retry(
        "r",
        Work("c", rng.uniform(0.3, 0.8), fail_times=99),
        attempts=rng.randint(2, 4),
    )
    parallel = Par(
        "f",
        [Work(f"b{i}", rng.uniform(0.2, 0.9)) for i in range(rng.randint(2, 4))],
    )
    return {"serial": serial, "retry": retry, "parallel": parallel}


def percentile(sorted_vals, q):
    idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return sorted_vals[idx]


def report(name, legacy_elapsed, fixed_elapsed):
    print(f"[{name}]  budget={BUDGET:.2f}  trials={TRIALS}")
    for label, vals in (("legacy(before)", legacy_elapsed), ("fixed(after) ", fixed_elapsed)):
        vals = sorted(vals)
        violations = sum(1 for v in vals if v > BUDGET + 1e-9)
        print(
            f"  {label}  min={vals[0]:.3f}  p50={percentile(vals, 0.50):.3f}"
            f"  p95={percentile(vals, 0.95):.3f}  max={vals[-1]:.3f}"
            f"  mean={statistics.fmean(vals):.3f}  over-budget={violations}/{len(vals)}"
        )
    print()


def main():
    rng = random.Random(20260928)
    legacy_elapsed = {"serial": [], "retry": [], "parallel": []}
    fixed_elapsed = {"serial": [], "retry": [], "parallel": []}
    for _ in range(TRIALS):
        for name, plan in make_plans(rng).items():
            legacy_elapsed[name].append(legacy_run(plan, FakeClock(), BUDGET).elapsed)
            fixed_elapsed[name].append(run(plan, FakeClock(), BUDGET).elapsed)
    for name in ("serial", "retry", "parallel"):
        report(name, legacy_elapsed[name], fixed_elapsed[name])


if __name__ == "__main__":
    main()
