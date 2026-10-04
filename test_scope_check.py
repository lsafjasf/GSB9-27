"""scope_check 自测。运行：python3 -m unittest -v"""

import unittest

from scope_check import (
    ScopeError,
    TokenRegistry,
    covers,
    make_scope,
    missing_scopes,
    parse_scope,
)


class TestCoversDecisionTable(unittest.TestCase):
    """逐行断言判定表（与 docs/decision_table.md 一一对应）。"""

    CASES = [
        # (granted, required, expected, 说明)
        ("read:invoice", "read:invoice", True, "精确匹配"),
        ("read:invoice", "write:invoice", False, "action 不同"),
        ("read:invoice", "read:user", False, "资源类型不同"),
        ("read:*", "read:invoice", True, "资源类型通配"),
        ("read:*", "write:invoice", False, "通配不跨段：action 仍需相等"),
        ("*:invoice", "read:invoice", True, "action 通配"),
        ("*:*", "read:invoice", True, "全通配"),
        ("read:invoice:inv-1001", "read:invoice:inv-1001", True, "资源级精确匹配"),
        ("read:invoice:inv-1001", "read:invoice:inv-1002", False, "资源不匹配"),
        ("read:invoice:*", "read:invoice:inv-1001", True, "资源 id 通配"),
        ("read:invoice:*", "read:invoice", False, "段数不等（3 段不覆盖 2 段）"),
        ("read:invoice", "read:invoice:inv-1001", False, "段数不等（2 段不覆盖 3 段）"),
        ("read:*", "read:invoice:inv-1001", False, "通配不递归：段数必须相等"),
        ("READ:invoice", "read:invoice", False, "区分大小写"),
    ]

    def test_decision_table(self):
        for granted, required, expected, note in self.CASES:
            with self.subTest(granted=granted, required=required, note=note):
                self.assertIs(covers(granted, required), expected)


class TestParseAndMakeScope(unittest.TestCase):
    def test_parse_valid(self):
        self.assertEqual(parse_scope("read:invoice:inv-1"), ("read", "invoice", "inv-1"))
        self.assertEqual(parse_scope("*"), ("*",))

    def test_parse_invalid(self):
        for bad in ("", "read:", ":invoice", "read::inv-1", ":"):
            with self.subTest(bad=bad):
                with self.assertRaises(ScopeError):
                    parse_scope(bad)

    def test_make_scope(self):
        self.assertEqual(make_scope("read", "invoice"), "read:invoice")
        self.assertEqual(make_scope("read", "invoice", "inv-1"), "read:invoice:inv-1")
        with self.assertRaises(ScopeError):
            make_scope("read", "invoice", "")  # 空资源 id 非法


class TestAuthorize(unittest.TestCase):
    def setUp(self):
        self.registry = TokenRegistry()

    def test_sufficient_scopes_allowed(self):
        token = self.registry.issue("alice", ["read:invoice", "write:invoice"])
        decision = self.registry.authorize(token.token_id, ["read:invoice", "write:invoice"])
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "ok")
        self.assertEqual(decision.matched_by["read:invoice"], "read:invoice")

    def test_missing_one_scope_denied(self):
        token = self.registry.issue("alice", ["read:invoice"])
        decision = self.registry.authorize(token.token_id, ["read:invoice", "write:invoice"])
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "scope_missing")
        self.assertEqual(decision.missing, ("write:invoice",))

    def test_wildcard_covers(self):
        token = self.registry.issue("admin", ["read:*"])
        self.assertTrue(self.registry.authorize(token.token_id, ["read:invoice"]).allowed)
        self.assertTrue(self.registry.authorize(token.token_id, ["read:user"]).allowed)
        # 通配不跨段：read:* 不覆盖 write:invoice
        denied = self.registry.authorize(token.token_id, ["write:invoice"])
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.missing, ("write:invoice",))

    def test_resource_level_restriction(self):
        token = self.registry.issue("bob", ["read:invoice:inv-1001"])
        ok = self.registry.authorize(token.token_id, ["read:invoice:inv-1001"])
        self.assertTrue(ok.allowed)
        denied = self.registry.authorize(token.token_id, ["read:invoice:inv-1002"])
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.missing, ("read:invoice:inv-1002",))

    def test_resource_wildcard_scope(self):
        token = self.registry.issue("carol", ["read:invoice:*"])
        self.assertTrue(self.registry.authorize(token.token_id, ["read:invoice:inv-1001"]).allowed)
        self.assertTrue(self.registry.authorize(token.token_id, ["read:invoice:inv-9999"]).allowed)

    def test_revocation_takes_effect_immediately(self):
        token = self.registry.issue("dave", ["read:invoice"])
        before = self.registry.authorize(token.token_id, ["read:invoice"])
        self.assertTrue(before.allowed)

        self.assertTrue(self.registry.revoke(token.token_id))

        after = self.registry.authorize(token.token_id, ["read:invoice"])
        self.assertFalse(after.allowed)
        self.assertEqual(after.reason, "token_revoked")

    def test_revoke_unknown_token(self):
        self.assertFalse(self.registry.revoke("no-such-token"))

    def test_expired_token_denied(self):
        token = self.registry.issue("eve", ["read:invoice"], ttl_seconds=60)
        future = token.expires_at + 1
        decision = self.registry.authorize(token.token_id, ["read:invoice"], now=future)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "token_expired")

    def test_expiry_alone_is_not_sufficient(self):
        # 核心反例：令牌未过期，但没有所需作用域，仍必须拒绝。
        token = self.registry.issue("mallory", ["read:public"])
        decision = self.registry.authorize(token.token_id, ["read:invoice"])
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "scope_missing")

    def test_unknown_token_denied(self):
        decision = self.registry.authorize("forged-token-id", ["read:invoice"])
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "token_unknown")

    def test_decision_log_records_allow_and_deny(self):
        token = self.registry.issue("alice", ["read:invoice"])
        self.registry.authorize(token.token_id, ["read:invoice"])
        self.registry.authorize(token.token_id, ["write:invoice"])
        self.assertEqual(len(self.registry.decision_log), 2)
        allow, deny = self.registry.decision_log
        self.assertTrue(allow.allowed)
        self.assertFalse(deny.allowed)
        self.assertEqual(deny.reason, "scope_missing")
        self.assertEqual(deny.missing, ("write:invoice",))
        self.assertEqual(deny.subject, "alice")


class TestMissingScopes(unittest.TestCase):
    def test_partial_coverage(self):
        self.assertEqual(
            missing_scopes(["read:invoice"], ["read:invoice", "write:invoice", "read:user"]),
            ["write:invoice", "read:user"],
        )

    def test_empty_required_is_allowed(self):
        self.assertEqual(missing_scopes([], []), [])


if __name__ == "__main__":
    unittest.main()
