"""Reproduce the timeout bug, then show the fix.

Scenario (budget = 100ms):
  Seq( fetch 80ms,
       Retry(flaky 60ms, fails twice, 3 attempts),
       Par(slow 90ms, fast 70ms) )

Legacy: every step/attempt/branch gets the full 100ms, nothing is shared.
Fixed : one 100ms budget propagates top-down; retries and branches draw
        from the same deadline.
"""
import sys

from timeoutctl import Par, Retry, Seq, Step, run_legacy, run_pipeline

BUDGET = 100


def build_pipeline():
    return Seq(
        "pipeline",
        Step("fetch", 80),
        Retry("retry", Step("flaky", 60, fail_times=2), attempts=3),
        Par("fanout", Step("slow", 90), Step("fast", 70)),
    )


def show(title, report):
    print("\n== %s ==" % title)
    for e in report.events:
        if e.start is None:
            print("  %-42s %s" % (e.path, e.status))
        else:
            print("  %-42s %-9s start=%4.0fms end=%4.0fms"
                  % (e.path, e.status, e.start, e.end))
    print("  --> status=%s elapsed=%.0fms budget=%.0fms %s"
          % (report.status, report.elapsed, report.budget,
             "(OVER BUDGET!)" if not report.within_budget else "(within budget)"))


def main():
    _, legacy = run_legacy(build_pipeline(), BUDGET)
    show("LEGACY (buggy): full 100ms budget per step / attempt / branch", legacy)

    _, fixed = run_pipeline(build_pipeline(), BUDGET)
    show("FIXED: one shared 100ms budget propagates top-down", fixed)

    print()
    bug_reproduced = legacy.elapsed > BUDGET
    fix_verified = fixed.elapsed <= BUDGET and fixed.status == "timeout"
    print("bug reproduced (legacy %dms > %dms): %s"
          % (legacy.elapsed, BUDGET, bug_reproduced))
    print("fix verified   (fixed  %dms <= %dms, timeout reported): %s"
          % (fixed.elapsed, BUDGET, fix_verified))
    return 0 if (bug_reproduced and fix_verified) else 1


if __name__ == "__main__":
    sys.exit(main())
