"""Self-test of the crash-consistency harness.

Scenarios:
  1. SafeKVStore, single put          -> must PASS (0 violations)
  2. SafeKVStore, two dependent puts  -> must PASS, exercising an
     explicitly declared INTERMEDIATE state
  3. BuggyKVStore, single put         -> must FAIL: the harness has to
     detect the known torn-write defect on every run

Exit code 0 iff all three expectations hold.
"""

import sys

from crash_harness import CrashHarness
from kvstore import SafeKVStore, BuggyKVStore, encode

OLD = "old-value-aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NEW = "new-value-bbbbbbbbbbbbbbbbbbbbbbbbbbbb"
A0, A1 = "a0-aaaaaaaaaaaaaaaaaaaaaaaaaaaa", "a1-AAAAAAAAAAAAAAAAAAAAAAAAAAAA"
B0, B1 = "b0-bbbbbbbbbbbbbbbbbbbbbbbbbbbb", "b1-BBBBBBBBBBBBBBBBBBBBBBBBBBBB"


def seed_single(disk):
    disk.files["db/main"] = encode({"k": OLD})


def put_workload(store):
    store.get("k")  # read-only op: demonstrates skip accounting
    store.put("k", NEW)


def single_checker(store):
    value = store.get("k")
    if value == OLD:
        return "OLD"
    if value == NEW:
        return "NEW"
    return ("UNEXPECTED", "k", value)


def seed_two(disk):
    disk.files["db/main"] = encode({"a": A0, "b": B0})


def two_phase_workload(store):
    store.put("a", A1)
    store.put("b", B1)


def two_phase_checker(store):
    state = (store.get("a"), store.get("b"))
    if state == (A0, B0):
        return "OLD"
    if state == (A1, B0):
        return ("INTERMEDIATE", "a-only")
    if state == (A1, B1):
        return "NEW"
    return ("UNEXPECTED", state)


def main():
    scenarios = [
        ("safe-kv / single put", seed_single, SafeKVStore, put_workload,
         single_checker, {"OLD", "NEW"}, True),
        ("safe-kv / two-phase put (explicit intermediate)", seed_two,
         SafeKVStore, two_phase_workload, two_phase_checker,
         {"OLD", "NEW", ("INTERMEDIATE", "a-only")}, True),
        ("buggy-kv / single put (in-place write, no fsync)", seed_single,
         BuggyKVStore, put_workload, single_checker, {"OLD", "NEW"}, False),
    ]

    failures = []
    reports = []
    for name, seed, cls, workload, checker, allowed, expect_ok in scenarios:
        report = CrashHarness(
            name=name,
            seed=seed,
            make_store=lambda disk, cls=cls: cls(disk),
            workload=workload,
            checker=checker,
            allowed=allowed,
        ).run()
        reports.append(report)
        print(report.render())
        print()
        if report.ok != expect_ok:
            failures.append(
                "%s: expected %s, got %s"
                % (name, "PASS" if expect_ok else "violations",
                   "PASS" if report.ok else "%d violations" % len(report.violations)))

    total_points = sum(r.points for r in reports)
    total_tested = sum(r.tested for r in reports)
    total_skipped = sum(len(r.skipped) for r in reports)
    total_violations = sum(len(r.violations) for r in reports)
    print("=" * 72)
    print("coverage totals: enumerated=%d tested=%d skipped=%d violations=%d"
          % (total_points, total_tested, total_skipped, total_violations))

    if failures:
        print("SELFTEST FAIL:")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("SELFTEST PASS: safe implementations clean, known defect detected "
          "(%d violations in buggy-kv)" % total_violations)
    return 0


if __name__ == "__main__":
    sys.exit(main())
