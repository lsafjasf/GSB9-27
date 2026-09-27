"""signed_url 自测：python3 -m unittest -v test_signed_url.py"""

import hmac
import unittest
from urllib.parse import parse_qsl, urlsplit

import signed_url as su

KEY = b"test-secret-key"
RES = "/download/report.pdf"


class FakeClock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


def issued_url(**overrides):
    args = dict(
        resource=RES,
        params=[("uid", "328"), ("uid", "999"), ("empty", ""), ("mode", "fast")],
        key=KEY,
        not_before=1000,
        expires_at=2000,
    )
    args.update(overrides)
    return su.build_url(**args)


def tamper_query(url, transform):
    """对 URL 查询串里的 (名,值) 列表做变换后重新拼接（保持原顺序）。"""
    split = urlsplit(url)
    pairs = transform(parse_qsl(split.query, keep_blank_values=True))
    query = "&".join(f"{n}={v}" for n, v in pairs)
    return f"{split.path}?{query}"


class DeterminismTest(unittest.TestCase):
    def test_param_order_irrelevant(self):
        u1 = su.build_url(RES, [("a", "1"), ("b", "2")], KEY,
                          not_before=0, expires_at=100)
        u2 = su.build_url(RES, [("b", "2"), ("a", "1")], KEY,
                          not_before=0, expires_at=100)
        self.assertEqual(u1, u2)

    def test_dict_and_list_input_equal(self):
        u1 = su.build_url(RES, {"a": "1", "b": ["2", "3"]}, KEY,
                          not_before=0, expires_at=100)
        u2 = su.build_url(RES, [("b", "2"), ("a", "1"), ("b", "3")], KEY,
                          not_before=0, expires_at=100)
        self.assertEqual(u1, u2)

    def test_duplicate_params_preserved(self):
        url = su.build_url(RES, [("k", "1"), ("k", "2"), ("k", "1")], KEY,
                           not_before=0, expires_at=100)
        clock = FakeClock(50)
        self.assertTrue(su.verify(url, KEY, clock=clock).ok)
        # 去掉一个重复的 k=1 即失败
        def drop_one(ps):
            ps = list(ps)
            ps.remove(("k", "1"))
            return ps
        bad = tamper_query(url, drop_one)
        self.assertEqual(su.verify(bad, KEY, clock=clock).reason,
                         su.REASON_BAD_SIGNATURE)

    def test_empty_value_distinct_from_absent(self):
        with_empty = su.build_url(RES, [("a", "")], KEY,
                                  not_before=0, expires_at=100)
        without = su.build_url(RES, [], KEY, not_before=0, expires_at=100)
        self.assertNotEqual(with_empty, without)
        clock = FakeClock(50)
        self.assertTrue(su.verify(with_empty, KEY, clock=clock).ok)

    def test_long_param_roundtrip(self):
        long_value = "x" * 100_000 + "中文/%&=+?"
        url = su.build_url(RES, [("big", long_value)], KEY,
                           not_before=0, expires_at=100)
        result = su.verify(url, KEY, clock=FakeClock(50))
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(dict(result.params)["big"], long_value)

    def test_reserved_param_rejected(self):
        for name in su.RESERVED_PARAMS:
            with self.assertRaises(su.IssueError):
                su.build_url(RES, [(name, "x")], KEY,
                             not_before=0, expires_at=100)


class TamperTest(unittest.TestCase):
    """每种篡改都必须校验失败，且 diff 能指出具体差异。"""

    def setUp(self):
        self.clock = FakeClock(1500)
        self.url = issued_url()
        self.original_params = [("uid", "328"), ("uid", "999"),
                                ("empty", ""), ("mode", "fast")]

    def check_tampered(self, bad_url, expect_diff=True):
        result = su.verify(bad_url, KEY, clock=self.clock)
        self.assertFalse(result.ok, f"篡改未被检出: {bad_url}")
        self.assertEqual(result.reason, su.REASON_BAD_SIGNATURE)
        if expect_diff:
            diff = result.diff_against(self.original_params)
            self.assertTrue(diff, "diff 应指出参数差异")
            return diff
        return None

    def test_add_param(self):
        bad = tamper_query(self.url, lambda ps: [("admin", "1")] + ps)
        diff = self.check_tampered(bad)
        self.assertEqual(diff.added, [("admin", "1")])

    def test_remove_param(self):
        bad = tamper_query(self.url,
                           lambda ps: [p for p in ps if p != ("mode", "fast")])
        diff = self.check_tampered(bad)
        self.assertEqual(diff.removed, [("mode", "fast")])

    def test_modify_value(self):
        bad = tamper_query(
            self.url,
            lambda ps: [("uid", "329") if p == ("uid", "328") else p
                        for p in ps])
        diff = self.check_tampered(bad)
        self.assertEqual(diff.changed_names, ["uid"])
        self.assertEqual(diff.added, [("uid", "329")])
        self.assertEqual(diff.removed, [("uid", "328")])

    def test_modify_expiry(self):
        bad = tamper_query(
            self.url,
            lambda ps: [("_exp", "9999") if p[0] == "_exp" else p for p in ps])
        self.check_tampered(bad, expect_diff=False)

    def test_modify_not_before(self):
        bad = tamper_query(
            self.url,
            lambda ps: [("_nb", "0") if p[0] == "_nb" else p for p in ps])
        self.check_tampered(bad, expect_diff=False)

    def test_modify_resource(self):
        bad = self.url.replace(RES, "/download/admin.pdf")
        self.check_tampered(bad, expect_diff=False)

    def test_modify_signature_char(self):
        bad = self.url[:-1] + ("0" if self.url[-1] != "0" else "1")
        self.check_tampered(bad, expect_diff=False)

    def test_reorder_params_still_valid(self):
        reordered = tamper_query(self.url, lambda ps: list(reversed(ps)))
        result = su.verify(reordered, KEY, clock=self.clock)
        self.assertTrue(result.ok, "参数顺序变化不应视为篡改")

    def test_malformed_links(self):
        no_sig = tamper_query(self.url,
                              lambda ps: [p for p in ps if p[0] != "_sig"])
        self.assertEqual(su.verify(no_sig, KEY, clock=self.clock).reason,
                         su.REASON_MALFORMED)
        bad_exp = tamper_query(
            self.url,
            lambda ps: [("_exp", "abc") if p[0] == "_exp" else p for p in ps])
        self.assertEqual(su.verify(bad_exp, KEY, clock=self.clock).reason,
                         su.REASON_MALFORMED)


class TimeWindowTest(unittest.TestCase):
    """合法窗口为闭区间 [nb - skew, exp + skew]，边界时刻有效。"""

    def setUp(self):
        self.url = issued_url()  # nb=1000, exp=2000

    def check(self, now, skew, ok, reason=su.REASON_OK):
        result = su.verify(self.url, KEY, clock=FakeClock(now), skew=skew)
        self.assertEqual(result.ok, ok, f"now={now} skew={skew}: {result}")
        self.assertEqual(result.reason, reason)

    def test_zero_skew_boundaries(self):
        self.check(999, 0, False, su.REASON_NOT_YET_VALID)
        self.check(1000, 0, True)   # 生效边界：有效
        self.check(2000, 0, True)   # 过期边界：有效
        self.check(2001, 0, False, su.REASON_EXPIRED)

    def test_skew_extends_window(self):
        self.check(1000 - 60 - 1, 60, False, su.REASON_NOT_YET_VALID)
        self.check(1000 - 60, 60, True)   # 偏移下界：有效
        self.check(2000 + 60, 60, True)   # 偏移上界：有效
        self.check(2000 + 60 + 1, 60, False, su.REASON_EXPIRED)

    def test_issue_uses_injected_clock(self):
        clock = FakeClock(5000)
        url = su.issue(RES, [("a", "1")], KEY, ttl=300, clock=clock)
        self.assertTrue(su.verify(url, KEY, clock=FakeClock(5000)).ok)
        self.assertTrue(su.verify(url, KEY, clock=FakeClock(5300)).ok)
        self.assertEqual(
            su.verify(url, KEY, clock=FakeClock(5301)).reason,
            su.REASON_EXPIRED)


class ConstantTimeTest(unittest.TestCase):
    """恒定时间性质的验证方式见 README「恒定时间比较」一节。"""

    def test_compare_digest_is_used(self):
        calls = []
        real = hmac.compare_digest

        def spy(a, b):
            calls.append((a, b))
            return real(a, b)

        url = issued_url()
        try:
            hmac.compare_digest = spy
            su.verify(url, KEY, clock=FakeClock(1500))
        finally:
            hmac.compare_digest = real
        self.assertEqual(len(calls), 1, "验签必须经由 hmac.compare_digest")
        self.assertEqual(len(calls[0][0]), 64)  # sha256 hex 等长比较

    def test_compare_digest_rejects_wrong_length_quickly_but_safely(self):
        # 长度不同的签名由 compare_digest 处理，不会抛异常也不会通过
        url = issued_url()
        split = urlsplit(url)
        pairs = [(n, v) for n, v in parse_qsl(split.query, keep_blank_values=True)
                 if n != "_sig"]
        pairs.append(("_sig", "deadbeef"))
        bad = f"{split.path}?" + "&".join(f"{n}={v}" for n, v in pairs)
        result = su.verify(bad, KEY, clock=FakeClock(1500))
        self.assertEqual(result.reason, su.REASON_BAD_SIGNATURE)


if __name__ == "__main__":
    unittest.main()
