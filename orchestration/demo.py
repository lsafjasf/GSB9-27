"""Runnable demo: failure isolation vs naive fail-fast, plus local retry.

Run: python3 -m orchestration.demo
"""

from __future__ import annotations

from .failure_isolation import (
    FailureIsolationEngine,
    Status,
    simulate_failfast,
)
from .test_failure_isolation import (
    all_fail_tasks,
    fanout_tasks,
    shared_dependency_tasks,
    single_point_tasks,
)


def ok(value):
    return lambda _results: value


SCENARIOS = [
    ("single point failure", single_point_tasks),
    ("fanout failure", fanout_tasks),
    ("shared dependency failure", shared_dependency_tasks),
    ("all tasks fail", all_fail_tasks),
]


def pct(rate: float) -> str:
    return f"{rate * 100:5.1f}%"


def main() -> None:
    print("== completion rate: naive fail-fast vs failure isolation ==")
    print(f"{'scenario':<28}{'before':>9}{'after':>9}")
    for label, builder in SCENARIOS:
        baseline = simulate_failfast(builder())
        isolated = FailureIsolationEngine(builder()).run()
        print(
            f"{label:<28}{pct(baseline.completion_rate):>9}"
            f"{pct(isolated.completion_rate):>9}"
        )

    print("\n== fanout failure: skip reasons (which upstream failed) ==")
    engine = FailureIsolationEngine(fanout_tasks())
    report = engine.run()
    print(
        f"succeeded={report.succeeded} failed={report.failed} "
        f"skipped={report.skipped} (skipped and failed are counted separately)"
    )
    for name, reasons in sorted(report.skip_reasons.items()):
        for upstream, message in reasons:
            print(f"  SKIP {name:<8} <- upstream {upstream!r} failed: {message}")

    print("\n== local retry after fixing 'hub' ==")
    fixed = engine.retry({"hub": ok("fixed-hub")})
    print(
        f"succeeded={fixed.succeeded} failed={fixed.failed} "
        f"skipped={fixed.skipped} completion={pct(fixed.completion_rate)}"
    )
    once = [
        name for name in ("root", "other1", "other2", "other3")
        if engine.run_count[name] == 1
    ]
    print("hub ran", engine.run_count["hub"], "time(s); untouched successes:", once)
    print("re-executed affected subgraph:", ["hub", "child1", "child2", "merged"])


if __name__ == "__main__":
    main()
