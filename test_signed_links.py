"""signed_links 自测：篡改检出、时间边界、恒定时间、参数边界情形。

运行：python3 -m unittest test_signed_links -v
"""

import hmac
import time
import unittest
import urllib.parse

import signed_links as sl


SECRET = b"test-secret-key-0123456789"
RESOURCE = "/files/report-2026q3.pdf"
NOT_BEFORE = 1_000
EXPIRES = 2_000


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def make_issuer(skew=0.0, now=1_500.0, **limits):
    clock = FakeClock(now)
    issuer = sl.SignedLinkIssuer(SECRET, clock=clock, clock_skew=skew, **limits)
    return issuer, clock


def base_params():
    return [("user", "alice"), ("disposition", "attachment"), ("tag", "a"), ("tag", "b")]


def sign_default(issuer):
    return issuer.sign(RESOURCE, base_params(), not_before=NOT_BEFORE, expires=EXPIRES)


class RoundTripTest(unittest.TestCase):
    def test_sign_and_verify_ok(self):
        issuer, _ = make_issuer()
        link = sign_default(issuer)
        result = issuer.verify(
            RESOURCE, link.params,
            not_before=NOT_BEFORE, expires=EXPIRES, signature=link.signature,
        )
        self.assertTrue(result.ok, result)

    def test_query_roundtrip(self):
        issuer, _ = make_issuer()
        link = sign_default(issuer)
        result = issuer.verify_query(RESOURCE, link.query())
        self.assertTrue(result.ok, result)

    def test_deterministic_issuance(self):
        issuer, _ = make_issuer()
        sig1 = sign_default(issuer).signature
        sig2 = sign_default(issuer).signature
        self.assertEqual(sig1, sig2)  # 同输入必得同签名

    def test_wrong_secret_fails(self):
        issuer, _ = make_issuer()
        other, _ = make_issuer()
        other._secret = b"another-secret"
        link = sign_default(issuer)
        result = other.verify(
            RESOURCE, link.params,
            not_before=NOT_BEFORE, expires=EXPIRES, signature=link.signature,
        )
        self.assertEqual(result.reason, "bad_signature")


class TamperDetectionTest(unittest.TestCase):
    """参数增/删/改、资源、时间、签名本身被改动都必须失败且能指出差异。"""

    def setUp(self):
        self.issuer, _ = make_issuer()
        self.link = sign_default(self.issuer)

    def check(self, params, resource=RESOURCE, expires=EXPIRES, signature=None):
        result = self.issuer.verify(
            resource, params,
            not_before=NOT_BEFORE, expires=expires,
            signature=signature or self.link.signature,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "bad_signature")
        return result

    def test_modify_value(self):
        tampered = [("user", "bob"), ("disposition", "attachment"), ("tag", "a"), ("tag", "b")]
        self.check(tampered)
        diff = sl.diff_params(self.link.params, tampered)
        self.assertTrue(diff)
        self.assertIn("user", diff.changed)
        self.assertEqual(diff.changed["user"]["expected"], ["alice"])
        self.assertEqual(diff.changed["user"]["actual"], ["bob"])

    def test_add_param(self):
        tampered = self.link.params + [("admin", "true")]
        self.check(tampered)
        diff = sl.diff_params(self.link.params, tampered)
        self.assertEqual(diff.added, {"admin": ["true"]})

    def test_remove_param(self):
        tampered = [p for p in self.link.params if p[0] != "disposition"]
        self.check(tampered)
        diff = sl.diff_params(self.link.params, tampered)
        self.assertEqual(diff.removed, {"disposition": ["attachment"]})

    def test_rename_param(self):
        tampered = [("role" if k == "user" else k, v) for k, v in self.link.params]
        self.check(tampered)
        diff = sl.diff_params(self.link.params, tampered)
        self.assertEqual(diff.added, {"role": ["alice"]})
        self.assertEqual(diff.removed, {"user": ["alice"]})

    def test_remove_one_duplicate(self):
        tampered = [p for p in self.link.params if p != ("tag", "b")]
        self.check(tampered)
        diff = sl.diff_params(self.link.params, tampered)
        self.assertEqual(diff.changed["tag"]["expected"], ["a", "b"])
        self.assertEqual(diff.changed["tag"]["actual"], ["a"])

    def test_change_resource(self):
        self.check(self.link.params, resource="/files/other.pdf")

    def test_extend_expiry(self):
        self.check(self.link.params, expires=EXPIRES + 3600)

    def test_swap_signature_from_other_link(self):
        other = self.issuer.sign(RESOURCE, [("user", "carol")],
                                 not_before=NOT_BEFORE, expires=EXPIRES)
        self.check(self.link.params, signature=other.signature)

    def test_truncated_signature(self):
        self.check(self.link.params, signature=self.link.signature[:-2])

    def test_diff_no_difference(self):
        self.assertFalse(sl.diff_params(self.link.params, list(self.link.params)))


class CanonicalizationTest(unittest.TestCase):
    """参数顺序、重复参数、空参数、超长参数。"""

    def setUp(self):
        self.issuer, _ = make_issuer()

    def test_order_irrelevant(self):
        sig1 = self.issuer.sign(RESOURCE, [("b", "2"), ("a", "1")],
                                not_before=NOT_BEFORE, expires=EXPIRES).signature
        sig2 = self.issuer.sign(RESOURCE, [("a", "1"), ("b", "2")],
                                not_before=NOT_BEFORE, expires=EXPIRES).signature
        self.assertEqual(sig1, sig2)

    def test_duplicate_order_irrelevant(self):
        sig1 = self.issuer.sign(RESOURCE, [("t", "1"), ("t", "2")],
                                not_before=NOT_BEFORE, expires=EXPIRES).signature
        sig2 = self.issuer.sign(RESOURCE, [("t", "2"), ("t", "1")],
                                not_before=NOT_BEFORE, expires=EXPIRES).signature
        self.assertEqual(sig1, sig2)

    def test_duplicate_multiplicity_matters(self):
        sig1 = self.issuer.sign(RESOURCE, [("t", "1"), ("t", "1")],
                                not_before=NOT_BEFORE, expires=EXPIRES).signature
        sig2 = self.issuer.sign(RESOURCE, [("t", "1")],
                                not_before=NOT_BEFORE, expires=EXPIRES).signature
        self.assertNotEqual(sig1, sig2)

    def test_empty_value_equals_bare_key(self):
        # "a" 与 "a=" 都解析为空值，签名相同
        sig1 = self.issuer.sign(RESOURCE, "a", not_before=NOT_BEFORE, expires=EXPIRES).signature
        sig2 = self.issuer.sign(RESOURCE, "a=", not_before=NOT_BEFORE, expires=EXPIRES).signature
        self.assertEqual(sig1, sig2)

    def test_empty_value_differs_from_absent(self):
        sig1 = self.issuer.sign(RESOURCE, [("a", "")],
                                not_before=NOT_BEFORE, expires=EXPIRES).signature
        sig2 = self.issuer.sign(RESOURCE, [],
                                not_before=NOT_BEFORE, expires=EXPIRES).signature
        self.assertNotEqual(sig1, sig2)

    def test_dict_and_pairs_equivalent(self):
        sig1 = self.issuer.sign(RESOURCE, {"a": "1", "b": "2"},
                                not_before=NOT_BEFORE, expires=EXPIRES).signature
        sig2 = self.issuer.sign(RESOURCE, [("a", "1"), ("b", "2")],
                                not_before=NOT_BEFORE, expires=EXPIRES).signature
        self.assertEqual(sig1, sig2)

    def test_dict_with_list_value(self):
        sig1 = self.issuer.sign(RESOURCE, {"t": ["1", "2"]},
                                not_before=NOT_BEFORE, expires=EXPIRES).signature
        sig2 = self.issuer.sign(RESOURCE, [("t", "1"), ("t", "2")],
                                not_before=NOT_BEFORE, expires=EXPIRES).signature
        self.assertEqual(sig1, sig2)

    def test_special_chars_canonical(self):
        params = [("na me", "v&a=l/u?e"), ("key", "100%")]
        link = self.issuer.sign(RESOURCE, params,
                                not_before=NOT_BEFORE, expires=EXPIRES)
        result = self.issuer.verify_query(RESOURCE, link.query())
        self.assertTrue(result.ok, result)

    def test_unicode_params(self):
        params = [("文件名", "报告.pdf")]
        link = self.issuer.sign(RESOURCE, params,
                                not_before=NOT_BEFORE, expires=EXPIRES)
        result = self.issuer.verify_query(RESOURCE, link.query())
        self.assertTrue(result.ok, result)

    def test_overlong_value_rejected_at_sign(self):
        with self.assertRaises(sl.ParamLimitError):
            self.issuer.sign(RESOURCE, [("k", "x" * 2049)],
                             not_before=NOT_BEFORE, expires=EXPIRES)

    def test_overlong_name_rejected_at_sign(self):
        with self.assertRaises(sl.ParamLimitError):
            self.issuer.sign(RESOURCE, [("k" * 129, "v")],
                             not_before=NOT_BEFORE, expires=EXPIRES)

    def test_too_many_params_rejected(self):
        with self.assertRaises(sl.ParamLimitError):
            self.issuer.sign(RESOURCE, [(f"k{i}", "v") for i in range(65)],
                             not_before=NOT_BEFORE, expires=EXPIRES)

    def test_overlong_value_fails_closed_at_verify(self):
        link = self.issuer.sign(RESOURCE, [("k", "v")],
                                not_before=NOT_BEFORE, expires=EXPIRES)
        result = self.issuer.verify(
            RESOURCE, [("k", "x" * 5000)],
            not_before=NOT_BEFORE, expires=EXPIRES, signature=link.signature,
        )
        self.assertEqual(result.reason, "invalid_params")

    def test_reserved_names_rejected(self):
        with self.assertRaises(sl.ReservedParamError):
            self.issuer.sign(RESOURCE, [("_sig", "forged")],
                             expires=EXPIRES)
        link = self.issuer.sign(RESOURCE, [("a", "1")], expires=EXPIRES)
        forged = link.query() + "&_sig=forged"
        result = self.issuer.verify_query(RESOURCE, forged)
        self.assertEqual(result.reason, "invalid_params")

    def test_missing_meta_params(self):
        result = self.issuer.verify_query(RESOURCE, "a=1&_exp=2000")
        self.assertEqual(result.reason, "invalid_params")


class TimeWindowTest(unittest.TestCase):
    """边界语义：not_before - skew <= now < expires + skew（下闭上开）。"""

    def verify_at(self, issuer, link, now):
        return issuer.verify(
            RESOURCE, link.params,
            not_before=link.not_before, expires=link.expires,
            signature=link.signature, now=now,
        )

    def test_boundaries_without_skew(self):
        issuer, _ = make_issuer(skew=0.0)
        link = sign_default(issuer)
        cases = [
            (NOT_BEFORE - 1, False, "not_yet_valid"),
            (NOT_BEFORE - 0.001, False, "not_yet_valid"),
            (NOT_BEFORE, True, None),          # 下界闭：恰好的生效时刻可用
            (NOT_BEFORE + 1, True, None),
            (EXPIRES - 1, True, None),
            (EXPIRES - 0.001, True, None),
            (EXPIRES, False, "expired"),       # 上界开：恰好的过期时刻即失效
            (EXPIRES + 1, False, "expired"),
        ]
        for now, ok, reason in cases:
            with self.subTest(now=now):
                result = self.verify_at(issuer, link, now)
                self.assertEqual(result.ok, ok, result)
                self.assertEqual(result.reason, reason)

    def test_boundaries_with_skew(self):
        skew = 5.0
        issuer, _ = make_issuer(skew=skew)
        link = sign_default(issuer)
        cases = [
            (NOT_BEFORE - skew - 0.001, False, "not_yet_valid"),
            (NOT_BEFORE - skew, True, None),   # 容忍下界
            (EXPIRES + skew - 0.001, True, None),
            (EXPIRES + skew, False, "expired"),  # 容忍上界之外才失效
        ]
        for now, ok, reason in cases:
            with self.subTest(now=now):
                result = self.verify_at(issuer, link, now)
                self.assertEqual(result.ok, ok, result)
                self.assertEqual(result.reason, reason)

    def test_injected_clock_controls_verification(self):
        issuer, clock = make_issuer(now=1_500.0)
        link = sign_default(issuer)
        self.assertTrue(issuer.verify(
            RESOURCE, link.params, not_before=NOT_BEFORE,
            expires=EXPIRES, signature=link.signature).ok)
        clock.now = 5_000.0  # 时钟前进 -> 过期
        result = issuer.verify(
            RESOURCE, link.params, not_before=NOT_BEFORE,
            expires=EXPIRES, signature=link.signature)
        self.assertEqual(result.reason, "expired")

    def test_expires_must_exceed_not_before(self):
        issuer, _ = make_issuer()
        with self.assertRaises(ValueError):
            issuer.sign(RESOURCE, [], not_before=2_000, expires=2_000)

    def test_negative_skew_rejected(self):
        with self.assertRaises(ValueError):
            sl.SignedLinkIssuer(SECRET, clock_skew=-1.0)


class ConstantTimeTest(unittest.TestCase):
    """恒定时间比较的两层验证：实现证明 + 统计冒烟。"""

    def test_uses_compare_digest(self):
        """monkeypatch 证明校验路径确实调用 hmac.compare_digest。"""
        issuer, _ = make_issuer()
        link = sign_default(issuer)
        calls = []
        original = hmac.compare_digest

        def spy(a, b):
            calls.append((a, b))
            return original(a, b)

        signed_links_hmac = sl.hmac
        try:
            signed_links_hmac.compare_digest = spy
            issuer.verify(
                RESOURCE, link.params, not_before=NOT_BEFORE,
                expires=EXPIRES, signature=link.signature)
        finally:
            signed_links_hmac.compare_digest = original
        self.assertEqual(len(calls), 1, "verify 必须且仅必须通过 compare_digest 比较")

    def test_compare_timing_smoke(self):
        """统计冒烟：'首位不同'与'末位不同'的签名比较耗时不应有数量级差异。

        注意：统计测试不能证明恒定时间（受噪声/调度影响），这里只作回归
        冒烟，根本保证来自 hmac.compare_digest 的 C 实现。
        """
        issuer, _ = make_issuer()
        link = sign_default(issuer)
        sig = link.signature
        flip_first = ("A" if sig[0] != "A" else "B") + sig[1:]
        flip_last = sig[:-1] + ("A" if sig[-1] != "A" else "B")

        def timed(signature, rounds=300):
            start = time.perf_counter()
            for _ in range(rounds):
                issuer.verify(RESOURCE, link.params, not_before=NOT_BEFORE,
                              expires=EXPIRES, signature=signature)
            return (time.perf_counter() - start) / rounds

        # 预热
        timed(flip_first, 100)
        timed(flip_last, 100)
        first = min(timed(flip_first) for _ in range(5))
        last = min(timed(flip_last) for _ in range(5))
        ratio = max(first, last) / max(min(first, last), 1e-12)
        self.assertLess(ratio, 2.0,
                        f"比较耗时差异过大: first={first:.6f}s last={last:.6f}s")


class DemoStyleTest(unittest.TestCase):
    """端到端：完整 URL 形态的签发与校验。"""

    def test_full_url_flow(self):
        issuer, _ = make_issuer()
        link = issuer.sign(
            "/files/report-2026q3.pdf",
            [("user", "alice"), ("filename", "报告.pdf")],
            not_before=NOT_BEFORE, expires=EXPIRES,
        )
        url = f"https://cdn.example.com{link.resource}?{link.query()}"
        parsed = urllib.parse.urlsplit(url)
        result = issuer.verify_query(parsed.path, parsed.query)
        self.assertTrue(result.ok, result)


if __name__ == "__main__":
    unittest.main()
