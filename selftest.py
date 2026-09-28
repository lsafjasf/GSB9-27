"""Self-test for the idempotency verification framework.

Runs the framework against three payment-service implementations whose
behaviour is known a priori, and asserts the framework classifies each
correctly:

  1. IdempotentPayment       -> IDEMPOTENT
  2. NonIdempotentPayment    -> NOT_IDEMPOTENT
  3. FakeIdempotentPayment   -> RETURN_CONSISTENT_BUT_EFFECTS_REPEATED

Writes report_sample.txt and report_sample.json next to this file.
"""

import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from idempotency_framework import (  # noqa: E402
    Category,
    EffectKind,
    SystemUnderTest,
    format_json_report,
    format_text_report,
    verify,
)

REQUEST = {
    "idempotency_key": "order-42-pay",
    "amount": 100,
    "currency": "CNY",
    "customer": "alice",
}


# ---------------------------------------------------------------------------
# 1. Truly idempotent: dedup by idempotency key, exactly-once effects.
# ---------------------------------------------------------------------------
class IdempotentPayment(SystemUnderTest):
    name = "idempotent-payment"

    def __init__(self):
        self._lock = threading.Lock()
        self._processed = {}   # idempotency_key -> response
        self._ledger = []
        self._balance = 0

    def handle(self, request, effects):
        key = request["idempotency_key"]
        with self._lock:
            if key in self._processed:
                return self._processed[key]          # replay: no side effects
            self._balance += request["amount"]
            receipt = {
                "receipt_id": f"rcpt-{key}",
                "amount": request["amount"],
                "status": "ok",
            }
            self._ledger.append(receipt)
            self._processed[key] = receipt
            effects.record(EffectKind.COUNTER, f"balance += {request['amount']}")
            effects.record(EffectKind.WRITE, f"ledger += {receipt['receipt_id']}")
            effects.record(EffectKind.NOTIFY, f"email to {request['customer']}")
            return receipt

    def snapshot_state(self):
        with self._lock:
            return (self._balance, len(self._ledger))


# ---------------------------------------------------------------------------
# 2. Non-idempotent: every call charges again, new receipt each time.
# ---------------------------------------------------------------------------
class NonIdempotentPayment(SystemUnderTest):
    name = "non-idempotent-payment"

    def __init__(self):
        self._lock = threading.Lock()
        self._ledger = []
        self._balance = 0
        self._seq = 0

    def handle(self, request, effects):
        with self._lock:
            self._seq += 1
            self._balance += request["amount"]
            receipt = {
                "receipt_id": f"rcpt-{self._seq}",   # new id every call
                "amount": request["amount"],
                "status": "ok",
            }
            self._ledger.append(receipt)
            effects.record(EffectKind.COUNTER, f"balance += {request['amount']}")
            effects.record(EffectKind.WRITE, f"ledger += {receipt['receipt_id']}")
            effects.record(EffectKind.NOTIFY, f"email to {request['customer']}")
            return receipt

    def snapshot_state(self):
        with self._lock:
            return (self._balance, len(self._ledger))


# ---------------------------------------------------------------------------
# 3. Fake idempotent: returns the cached response, but still performs the
#    side effects on every call.
# ---------------------------------------------------------------------------
class FakeIdempotentPayment(SystemUnderTest):
    name = "fake-idempotent-payment"

    def __init__(self):
        self._lock = threading.Lock()
        self._cache = {}       # idempotency_key -> cached response
        self._ledger = []
        self._balance = 0

    def handle(self, request, effects):
        key = request["idempotency_key"]
        with self._lock:
            if key not in self._cache:
                self._cache[key] = {
                    "receipt_id": f"rcpt-{key}",
                    "amount": request["amount"],
                    "status": "ok",
                }
            # BUG: side effects run unconditionally on every call.
            self._balance += request["amount"]
            self._ledger.append(self._cache[key])
            effects.record(EffectKind.COUNTER, f"balance += {request['amount']}")
            effects.record(EffectKind.WRITE, "ledger += duplicate")
            effects.record(EffectKind.NOTIFY, f"email to {request['customer']}")
            return self._cache[key]                  # ...but response looks stable

    def snapshot_state(self):
        with self._lock:
            return (self._balance, len(self._ledger))


# ---------------------------------------------------------------------------
# Self-test driver
# ---------------------------------------------------------------------------
CASES = [
    (IdempotentPayment, Category.IDEMPOTENT),
    (NonIdempotentPayment, Category.NOT_IDEMPOTENT),
    (FakeIdempotentPayment, Category.RETURN_CONSISTENT_BUT_EFFECTS_REPEATED),
]


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    text_reports, json_reports = [], []
    failures = 0

    for cls, expected in CASES:
        verdict = verify(cls(), dict(REQUEST))
        ok = verdict.category == expected
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {verdict.sut_name:<28} "
              f"expected={expected.value:<42} got={verdict.category.value}")
        if not ok:
            failures += 1
        text_reports.append(format_text_report(verdict))
        json_reports.append(json.loads(format_json_report(verdict)))

    txt_path = os.path.join(here, "report_sample.txt")
    json_path = os.path.join(here, "report_sample.json")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(text_reports))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_reports, f, indent=2, ensure_ascii=False, default=repr)
    print(f"\nreports written: {txt_path}, {json_path}")

    if failures:
        print(f"SELF-TEST FAILED: {failures} misclassified")
        return 1
    print("SELF-TEST PASSED: all 3 known implementations classified correctly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
