"""差分测试：逐调用点对比重构前后的重试次数、总等待时间与最终结果。

未修正的调用点：所有场景下 RunResult 必须完全一致。
刻意修正的调用点：瞬态错误场景行为一致；不可重试错误场景断言新行为
（快速失败），并记录遗留行为作为修正依据。
"""
import unittest

from errors import (
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    TransientError,
    ValidationError,
)
from legacy import (
    audit as legacy_audit,
    billing as legacy_billing,
    inventory as legacy_inventory,
    notifications as legacy_notifications,
    payments as legacy_payments,
    pricing as legacy_pricing,
    reports as legacy_reports,
    search as legacy_search,
    session as legacy_session,
    shipping as legacy_shipping,
    usersync as legacy_usersync,
    webhook as legacy_webhook,
)
from services import (
    audit,
    billing,
    inventory,
    notifications,
    payments,
    pricing,
    reports,
    search,
    session,
    shipping,
    usersync,
    webhook,
)
from tests.fakes import run_call_site

OK = {"status": 200}

SCENARIOS = {
    "first_ok": [OK],
    "flaky_then_ok": [TransientError(), TransientError(), OK],
    "exhaust_transient": [TransientError()] * 30,
    "rate_limited": [RateLimitError()] * 30,
    "validation_error": [ValidationError("bad param")] * 30,
    "permission_denied": [PermissionDeniedError()] * 30,
    "not_found": [NotFoundError()] * 30,
}

# (名称, 遗留函数, 重构后函数, 业务参数, 是否本次刻意修正)
CALL_SITES = [
    ("payments.get_balance", legacy_payments.get_balance,
     payments.get_balance, ("acct-1",), False),
    ("payments.charge", legacy_payments.charge,
     payments.charge, ("order-1", 99.5), False),
    ("inventory.reserve", legacy_inventory.reserve,
     inventory.reserve, ("SKU-1", 3), False),
    ("notifications.send_email", legacy_notifications.send_email,
     notifications.send_email, ("a@b.c", "hi"), True),
    ("usersync.sync_user", legacy_usersync.sync_user,
     usersync.sync_user, ("user-1",), False),
    ("reports.export_report", legacy_reports.export_report,
     reports.export_report, ("rpt-1",), False),
    ("billing.create_invoice", legacy_billing.create_invoice,
     billing.create_invoice, ("order-1",), True),
    ("search.query", legacy_search.query,
     search.query, ("keyword",), False),
    ("webhook.deliver", legacy_webhook.deliver,
     webhook.deliver, ("hook-1", {"x": 1}), False),
    ("audit.log_event", legacy_audit.log_event,
     audit.log_event, ({"action": "login"},), True),
    ("pricing.get_quote", legacy_pricing.get_quote,
     pricing.get_quote, ("SKU-1",), False),
    ("shipping.create_label", legacy_shipping.create_label,
     shipping.create_label, ("order-1",), True),
    ("session.refresh_token", legacy_session.refresh_token,
     session.refresh_token, ("sess-1",), False),
]

# 刻意修正的调用点：在这些场景下行为有意改变（不可重试错误不再重试）
CHANGED_SCENARIOS = {
    "notifications.send_email": {
        "validation_error",
        "permission_denied",
        "not_found",
    },
    "billing.create_invoice": {
        "validation_error",
        "permission_denied",
        "not_found",
    },
    "audit.log_event": {"validation_error", "permission_denied", "not_found"},
    "shipping.create_label": {"not_found"},
}


class TestBehavioralEquivalence(unittest.TestCase):
    """未修正场景：重构前后 attempts / sleeps / outcome 完全一致。"""

    def test_equivalent_scenarios(self):
        failures = []
        for name, legacy_fn, modern_fn, args, _changed in CALL_SITES:
            changed = CHANGED_SCENARIOS.get(name, set())
            for scenario, outcomes in SCENARIOS.items():
                if scenario in changed:
                    continue
                before = run_call_site(legacy_fn, args, outcomes)
                after = run_call_site(modern_fn, args, outcomes)
                if before != after:
                    failures.append(
                        "%s [%s]\n  before=%r\n  after=%r"
                        % (name, scenario, before, after)
                    )
        self.assertEqual(failures, [])


class TestIntentionalFixes(unittest.TestCase):
    """刻意修正场景：新代码快速失败；遗留行为仅作对照记录。"""

    def _check_fix(self, name, legacy_fn, modern_fn, args, scenario, exc):
        outcomes = [exc] * 30
        before = run_call_site(legacy_fn, args, outcomes)
        after = run_call_site(modern_fn, args, outcomes)
        self.assertGreater(
            before.attempts, 1, "%s: 遗留代码应当重试了不可重试错误" % name
        )
        self.assertEqual(after.attempts, 1, "%s: 不可重试错误必须快速失败" % name)
        self.assertEqual(after.sleeps, [])
        self.assertEqual(after.outcome, ("error", type(exc).__name__))
        self.assertEqual(before.outcome, after.outcome)

    def test_send_email_stops_retrying_non_retryable_errors(self):
        for scenario, exc in (
            ("validation_error", ValidationError()),
            ("permission_denied", PermissionDeniedError()),
            ("not_found", NotFoundError()),
        ):
            self._check_fix(
                "notifications.send_email",
                legacy_notifications.send_email,
                notifications.send_email,
                ("a@b.c", "hi"),
                scenario,
                exc,
            )

    def test_create_invoice_stops_retrying_non_retryable_errors(self):
        for scenario, exc in (
            ("validation_error", ValidationError()),
            ("permission_denied", PermissionDeniedError()),
            ("not_found", NotFoundError()),
        ):
            self._check_fix(
                "billing.create_invoice",
                legacy_billing.create_invoice,
                billing.create_invoice,
                ("order-1",),
                scenario,
                exc,
            )

    def test_log_event_stops_retrying_non_retryable_errors(self):
        for scenario, exc in (
            ("validation_error", ValidationError()),
            ("permission_denied", PermissionDeniedError()),
            ("not_found", NotFoundError()),
        ):
            self._check_fix(
                "audit.log_event",
                legacy_audit.log_event,
                audit.log_event,
                ({"action": "login"},),
                scenario,
                exc,
            )

    def test_create_label_stops_retrying_not_found(self):
        self._check_fix(
            "shipping.create_label",
            legacy_shipping.create_label,
            shipping.create_label,
            ("order-1",),
            "not_found",
            NotFoundError(),
        )


if __name__ == "__main__":
    unittest.main()
