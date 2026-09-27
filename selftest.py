"""
Self-test: run the idempotency framework against three known implementations
and assert that every pattern is judged correctly.

Usage:
    python3 selftest.py [--report REPORT_JSON]

Exit code 0 iff all expectations hold. A sample report (JSON) is written to
REPORT_JSON (default: report_sample.json).
"""

from __future__ import annotations

import argparse
import json
import sys

from idempotency_framework import (
    NOT_IDEMPOTENT,
    RETURN_CONSISTENT_BUT_SIDE_EFFECTS_REPEATED,
    TRULY_IDEMPOTENT,
    IdempotencyChecker,
)
from targets import (
    FakeIdempotentPaymentTarget,
    IdempotentPaymentTarget,
    NonIdempotentPaymentTarget,
)

# expected verdict per (target, pattern)
EXPECTATIONS = {
    "IdempotentPaymentTarget": {
        "sequential_replay": TRULY_IDEMPOTENT,
        "concurrent_replay": TRULY_IDEMPOTENT,
        "timeout_then_retry": TRULY_IDEMPOTENT,
        "overall": TRULY_IDEMPOTENT,
    },
    "NonIdempotentPaymentTarget": {
        "sequential_replay": NOT_IDEMPOTENT,
        "concurrent_replay": NOT_IDEMPOTENT,
        "timeout_then_retry": NOT_IDEMPOTENT,
        "overall": NOT_IDEMPOTENT,
    },
    "FakeIdempotentPaymentTarget": {
        "sequential_replay": RETURN_CONSISTENT_BUT_SIDE_EFFECTS_REPEATED,
        "concurrent_replay": RETURN_CONSISTENT_BUT_SIDE_EFFECTS_REPEATED,
        "timeout_then_retry": RETURN_CONSISTENT_BUT_SIDE_EFFECTS_REPEATED,
        "overall": RETURN_CONSISTENT_BUT_SIDE_EFFECTS_REPEATED,
    },
}

TARGETS = [
    IdempotentPaymentTarget,
    NonIdempotentPaymentTarget,
    FakeIdempotentPaymentTarget,
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="report_sample.json",
                        help="where to write the sample report JSON")
    args = parser.parse_args()

    reports = []
    failures = []

    for target_cls in TARGETS:
        checker = IdempotencyChecker(target_cls())
        report = checker.run_all()
        reports.append(report.to_dict())

        name = report.target_name
        expected = EXPECTATIONS[name]
        print(f"\n=== {name} ===")
        for p in report.patterns:
            ok = p.verdict == expected[p.pattern]
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {p.pattern:<18} -> {p.verdict}")
            print(f"         evidence: side_effects={p.side_effect_count}, "
                  f"return_consistent={p.return_consistent}")
            print(f"         rationale: {p.rationale}")
            if not ok:
                failures.append(
                    f"{name}/{p.pattern}: expected "
                    f"{expected[p.pattern]}, got {p.verdict}")
        ok = report.overall_verdict == expected["overall"]
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {'overall':<18} -> {report.overall_verdict}")
        if not ok:
            failures.append(f"{name}/overall: expected "
                            f"{expected['overall']}, got "
                            f"{report.overall_verdict}")

    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump({"framework": "idempotency-checker",
                   "reports": reports}, fh, indent=2, ensure_ascii=False)
    print(f"\nsample report written to {args.report}")

    if failures:
        print(f"\n{len(failures)} expectation(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nall expectations passed: framework judges all three known "
          "implementations correctly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
