"""差分测试：逐调用点对比重构前后的重试次数、总等待时间与最终结果。

未修正的调用点：所有场景下 RunResult 必须完全一致。
刻意修正的调用点：瞬态/限流场景行为一致；不可重试错误场景断言新行为
（快速失败），并断言遗留行为确实在重试，作为修正依据。
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
    currency as legacy_currency,
    geo as legacy_geo,
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
    currency,
    geo,
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

# (名称, 遗留函数, 重构后函数, 业务参数)
CALL_SITES = [
    ("payments.get_balance", legacy_payments.get_balance,
     payments.get_balance, ("acct-1",)),
    ("payments.charge", legacy_payments.charge,
     payments.charge, ("order-1", 99.5)),
    ("inventory.reserve", legacy_inventory.reserve,
     inventory.reserve, ("SKU-1", 3)),
    ("notifications.send_email", legacy_notifications.send_email,
     notifications.send_email, ("a@b.c", "hi")),
    ("usersync.sync_user", legacy_usersync.sync_user,
     usersync.sync_user, ("user-1",)),
    ("reports.export_report", legacy_reports.export_report,
     reports.export_report, ("rpt-1",)),
    ("billing.create_invoice", legacy_billing.create_invoice,
     billing.create_invoice, ("order-1",)),
    ("search.query", legacy_search.query,
     search.query, ("keyword",)),
    ("webhook.deliver", legacy_webhook.deliver,
     webhook.deliver, ("hook-1", {"x": 1})),
    ("audit.log_event", legacy_audit.log_event,
     audit.log_event, ({"action": "login"},)),
    ("pricing.get_quote", legacy_pricing.get_quote,
     pricing.get_quote, ("SKU-1",)),
    ("shipping.create_label", legacy_shipping.create_label,
     shipping.create_label, ("order-1",)),
    ("session.refresh_token", legacy_session.refresh_token,
     session.refresh_token, ("sess-1",)),
    ("geo.locate_ip", legacy_geo.locate_ip,
     geo.locate_ip, ("1.2.3.4",)),
    ("currency.get_rate", legacy_currency.get_rate,
     currency.get_rate, ("USD/CNY",)),
]

# 刻意修正的调用点：这些场景下行为有意改变（不可重试错误不再重试）
CHANGED_SCENARIOS = {
    "notifications.send_email": {
        "validation_error", "permission_denied", "not_found",
    },
    "billing.create_invoice": {
        "validation_error", "permission_denied", "not_found",
    },
    "audit.log_event": {
        "validation_error", "permission_denied", "not_found",
    },
    "shipping.create_label": {"not_found"},
}


class TestBehavioralEquivalence(unittest.TestCase):
    """未修正场景：重构前后 attempts / sleeps / outcome 完全一致。"""

    def test_equivalent_scenarios(self):
        failures = []
        for name, legacy_fn, modern_fn, args in CALL_SITES:
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
    """修正场景：不可重试错误快速失败；遗留行为作为对照保留。"""

    def test_fixed_call_sites_fail_fast(self):
        failures = []
        for name, legacy_fn, modern_fn, args in CALL_SITES:
            for scenario in CHANGED_SCENARIOS.get(name, ()):
                outcomes = SCENARIOS[scenario]
                before = run_call_site(legacy_fn, args, outcomes)
                after = run_call_site(modern_fn, args, outcomes)
                # 遗留行为确实在重试（修正的依据）
                if before.attempts <= 1:
                    failures.append(
                        "%s [%s] 遗留行为未重试，无需修正: %r"
                        % (name, scenario, before)
                    )
                # 新行为：第 1 次即失败、无等待、异常类型不变
                if after.attempts != 1 or after.sleeps != []:
                    failures.append(
                        "%s [%s] 未快速失败: %r" % (name, scenario, after)
                    )
                if after.outcome != before.outcome:
                    failures.append(
                        "%s [%s] 最终结果类型改变: %r -> %r"
                        % (name, scenario, before.outcome, after.outcome)
                    )
        self.assertEqual(failures, [])

    def test_fixed_call_sites_still_retry_transient(self):
        # 修正只影响不可重试错误；瞬态/限流场景行为保持不变
        for name, legacy_fn, modern_fn, args in CALL_SITES:
            if name not in CHANGED_SCENARIOS:
                continue
            for scenario in ("flaky_then_ok", "exhaust_transient",
                             "rate_limited"):
                outcomes = SCENARIOS[scenario]
                before = run_call_site(legacy_fn, args, outcomes)
                after = run_call_site(modern_fn, args, outcomes)
                self.assertEqual(
                    before, after,
                    "%s [%s] 瞬态场景行为不一致" % (name, scenario),
                )


if __name__ == "__main__":
    unittest.main()
