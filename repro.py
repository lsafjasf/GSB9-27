"""Reproduce the timeout-budget bug and show the fix side by side.

Runs identical plans against legacy_timeout (buggy) and budget_timeout
(fixed) with an injected FakeClock, and prints the elapsed time of each.
Exit code 0 (the repro *demonstrates* the bug; assertions live in the
unit tests).
"""
from budget_timeout import FakeClock, Par, Retry, Seq, Work, run
from legacy_timeout import run as legacy_run

BUDGET = 1.0


def show(title, plan):
    legacy = legacy_run(plan, FakeClock(), BUDGET)
    fixed = run(plan, FakeClock(), BUDGET)
    print(f"{title}")
    print(f"  budget           : {BUDGET:.2f}")
    print(f"  legacy elapsed   : {legacy.elapsed:.2f}  status={legacy.status.value}"
          f"  <-- {'VIOLATES budget' if legacy.elapsed > BUDGET + 1e-9 else 'within budget'}")
    print(f"  fixed  elapsed   : {fixed.elapsed:.2f}  status={fixed.status.value}"
          f"  <-- {'VIOLATES budget' if fixed.elapsed > BUDGET + 1e-9 else 'within budget'}")
    print(f"  fixed  steps     : "
          + ", ".join(f"{s.name}:{s.status.value}" for s in fixed.steps))
    print()


def main():
    show(
        "serial: 5 steps x 0.60s, budget 1.00s",
        Seq("pipeline", [Work(f"step{i}", 0.60) for i in range(5)]),
    )
    show(
        "retry: 3 attempts x 0.60s (always failing), budget 1.00s",
        Retry("flaky", Work("call", 0.60, fail_times=99), attempts=3),
    )
    show(
        "nested retry: 2 x (2 attempts x 0.60s), budget 1.00s",
        Retry("outer", Retry("inner", Work("call", 0.60, fail_times=99), attempts=2), attempts=2),
    )
    show(
        "parallel: 3 branches x 0.60s, budget 1.00s",
        Par("fanout", [Work(f"branch{i}", 0.60) for i in range(3)]),
    )


if __name__ == "__main__":
    main()
