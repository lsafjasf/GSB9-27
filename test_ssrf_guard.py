"""ssrf_guard 绕过用例集与自测。

运行：python3 test_ssrf_guard.py -v
所有用例不依赖真实外网：DNS 用本地 mock 解析器，HTTP 用 mock 传输层。
"""

import socket
import time
import unittest

from ssrf_guard import (
    FetchResult,
    blocked_reason,
    parse_ip_literal,
    safe_fetch,
    validate_url,
)

PUBLIC_IP = "93.184.216.34"          # example.com 的公网地址
PUBLIC_V6 = "2606:4700:4700::1111"   # 1.1.1.1 的公网 IPv6


class MockResolver:
    """本地 mock 解析器：table 命中返回 IP 列表，未命中抛 gaierror。"""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, host, port):
        self.calls.append(host)
        if host in self.table:
            return self.table[host]
        raise socket.gaierror(8, f"mock: 未知主机 {host}")


class MockTransport:
    """mock 传输层：记录每次请求的 (url, pinned_ip)，按路由表返回响应。"""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, method, url, pinned_ip, timeout):
        self.requests.append((url, pinned_ip))
        if url in self.routes:
            return self.routes[url]
        raise OSError(f"mock: 未配置路由 {url}")


def make_resolver():
    return MockResolver({
        "public.example": [PUBLIC_IP],
        "public-v6.example": [PUBLIC_V6],
        "localhost": ["127.0.0.1"],
        "internal.example": ["10.1.2.3"],
        "metadata.example": ["169.254.169.254"],
        "dual.example": [PUBLIC_IP, "192.168.1.1"],   # 一条公网一条内网
        "v6mapped.example": ["::ffff:127.0.0.1"],
        "rebind.example": [PUBLIC_IP],                # 校验时返回公网
    })


class TestLiteralBypass(unittest.TestCase):
    """IP 字面量的各种伪装写法，必须全部拒绝。"""

    BLOCKED_URLS = [
        # 回环
        "http://127.0.0.1/",
        "http://127.0.0.1./",            # 末尾点
        "http://127.1/",                 # 省略段
        "http://127.0.1/",
        "http://2130706433/",            # 十进制整数
        "http://0x7f000001/",            # 十六进制整数
        "http://0x7f.0x0.0x0.0x1/",      # 分段十六进制
        "http://0177.0.0.1/",            # 八进制
        "http://0177.1/",                # 八进制 + 省略段
        "http://0/",                     # 0.0.0.0
        "http://0.0.0.0/",
        # 私网
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://172.31.255.254/",
        "http://192.168.1.1/",
        # 链路本地 / 云元数据
        "http://169.254.169.254/latest/meta-data",
        # CGNAT / 保留 / 组播 / 文档段
        "http://100.64.0.1/",
        "http://192.0.2.1/",
        "http://198.18.0.1/",
        "http://224.0.0.1/",
        "http://240.0.0.1/",
        "http://255.255.255.255/",
        # IPv6
        "http://[::1]/",
        "http://[::]/",
        "http://[::ffff:127.0.0.1]/",    # IPv4 映射
        "http://[::ffff:10.0.0.1]/",
        "http://[::127.0.0.1]/",         # IPv4 兼容（::/96）
        "http://[fe80::1]/",             # 链路本地
        "http://[fc00::1]/",             # ULA
        "http://[ff02::1]/",             # 组播
        "http://[64:ff9b::7f00:1]/",     # NAT64 内嵌 127.0.0.1
        "http://[2002:0a00:1::]/",       # 6to4 内嵌 10.0.0.1
        "http://[2001:db8::1]/",         # 文档段
        "http://[100::1]/",              # discard-only
    ]

    def test_all_blocked(self):
        for url in self.BLOCKED_URLS:
            with self.subTest(url=url):
                r = validate_url(url, resolver=make_resolver())
                self.assertFalse(r.ok, f"应被拒绝: {url}")

    def test_public_allowed(self):
        for url in ("http://93.184.216.34/", "https://[2606:4700:4700::1111]/"):
            with self.subTest(url=url):
                r = validate_url(url, resolver=make_resolver())
                self.assertTrue(r.ok, f"应放行: {url} -> {r.reason}")


class TestDnsBypass(unittest.TestCase):
    """域名层面的绕过：解析到内网、解析器差异写法。"""

    def test_domain_resolves_internal(self):
        r = validate_url("http://internal.example/", resolver=make_resolver())
        self.assertFalse(r.ok)
        self.assertIn("10.1.2.3", r.reason)

    def test_localhost_name(self):
        r = validate_url("http://localhost/", resolver=make_resolver())
        self.assertFalse(r.ok)

    def test_cloud_metadata_domain(self):
        r = validate_url("http://metadata.example/latest/", resolver=make_resolver())
        self.assertFalse(r.ok)

    def test_one_bad_record_fails_all(self):
        """多条 A 记录中只要有一条内网即拒绝（防轮询命中内网）。"""
        r = validate_url("http://dual.example/", resolver=make_resolver())
        self.assertFalse(r.ok)

    def test_dns_returns_v6_mapped(self):
        r = validate_url("http://v6mapped.example/", resolver=make_resolver())
        self.assertFalse(r.ok)

    def test_unknown_domain_rejected(self):
        r = validate_url("http://no-such-host.example/", resolver=make_resolver())
        self.assertFalse(r.ok)

    def test_public_domain_allowed(self):
        r = validate_url("http://public.example/cb", resolver=make_resolver())
        self.assertTrue(r.ok)
        self.assertEqual(r.ips, (PUBLIC_IP,))

    def test_numeric_name_via_dns(self):
        """getaddrinfo 会把 '2130706433' 之类当 IP 解析；
        即便绕过字面量解析，解析结果仍会被判定。"""
        resolver = MockResolver({"2130706433": ["127.0.0.1"]})
        r = validate_url("http://2130706433/", resolver=resolver)
        self.assertFalse(r.ok)


class TestUserinfoDeception(unittest.TestCase):
    """user@host 用户名欺骗。"""

    def test_userinfo_hiding_internal_host(self):
        r = validate_url("http://www.trusted.com@127.0.0.1/", resolver=make_resolver())
        self.assertFalse(r.ok)

    def test_userinfo_default_rejected_even_public(self):
        r = validate_url("http://user:pass@93.184.216.34/", resolver=make_resolver())
        self.assertFalse(r.ok)
        self.assertIn("userinfo", r.reason)

    def test_userinfo_real_host_is_validated(self):
        """允许 userinfo 时，校验对象是真正的 host 而非用户名部分。"""
        r = validate_url("http://www.trusted.com@127.0.0.1/",
                         resolver=make_resolver(), allow_userinfo=True)
        self.assertFalse(r.ok)  # 真实 host 是 127.0.0.1
        r2 = validate_url("http://127.0.0.1@public.example/",
                          resolver=make_resolver(), allow_userinfo=True)
        self.assertTrue(r2.ok)  # 真实 host 是 public.example，连接也去那里

    def test_backslash_confusion(self):
        for url in ("http://public.example\\@127.0.0.1/",
                    "http://127.0.0.1\\@public.example/"):
            with self.subTest(url=url):
                r = validate_url(url, resolver=make_resolver())
                self.assertFalse(r.ok)


class TestSchemeAndPort(unittest.TestCase):
    def test_schemes(self):
        for url in ("file:///etc/passwd", "gopher://127.0.0.1:6379/_INFO",
                    "dict://127.0.0.1:11211/", "ftp://10.0.0.1/",
                    "//127.0.0.1/x", "http:///path"):
            with self.subTest(url=url):
                self.assertFalse(validate_url(url, resolver=make_resolver()).ok)

    def test_port_whitelist(self):
        self.assertFalse(validate_url("http://93.184.216.34:22/").ok)
        self.assertFalse(validate_url("http://127.0.0.1:6379/").ok)
        self.assertTrue(validate_url("http://93.184.216.34:8080/",
                                     allowed_ports=(80, 443, 8080)).ok)

    def test_bad_port(self):
        self.assertFalse(validate_url("http://public.example:notaport/").ok)


class TestRedirectBypass(unittest.TestCase):
    """重定向后再指向内网：每一跳都必须重新校验。"""

    def test_redirect_to_internal_blocked_before_connect(self):
        resolver = make_resolver()
        transport = MockTransport({
            "http://public.example/start": (
                302, {"location": "http://169.254.169.254/latest/meta-data"}, b""),
        })
        result = safe_fetch("http://public.example/start",
                            resolver=resolver, transport=transport)
        self.assertFalse(result.ok)
        self.assertIn("169.254.169.254", result.reason)
        # 关键断言：内网地址从未被连接
        self.assertEqual([u for u, _ in transport.requests],
                         ["http://public.example/start"])

    def test_redirect_chain_to_domain_internal(self):
        resolver = make_resolver()
        transport = MockTransport({
            "http://public.example/a": (301, {"location": "/b"}, b""),
            "http://public.example/b": (
                302, {"location": "http://internal.example/secret"}, b""),
        })
        result = safe_fetch("http://public.example/a",
                            resolver=resolver, transport=transport)
        self.assertFalse(result.ok)
        self.assertEqual(len(transport.requests), 2)  # 第三跳未发起

    def test_redirect_loop_limited(self):
        transport = MockTransport({
            "http://public.example/loop": (
                302, {"location": "/loop"}, b""),
        })
        result = safe_fetch("http://public.example/loop",
                            resolver=make_resolver(), transport=transport)
        self.assertFalse(result.ok)
        self.assertIn("重定向", result.reason)

    def test_clean_redirect_allowed(self):
        transport = MockTransport({
            "http://public.example/a": (301, {"location": "/b"}, b""),
            "http://public.example/b": (200, {}, b"hello"),
        })
        result = safe_fetch("http://public.example/a",
                            resolver=make_resolver(), transport=transport)
        self.assertTrue(result.ok)
        self.assertEqual(result.body, b"hello")
        self.assertEqual(result.hops, 1)


class TestDnsRebinding(unittest.TestCase):
    """DNS 重绑定：校验与连接必须使用同一次解析结果（IP 钉扎）。"""

    def test_connection_pinned_to_validated_ip(self):
        """解析器第二次调用会返回内网地址（模拟 TTL 到期后记录被改）；
        safe_fetch 只解析一次并钉扎，连接到的必须是校验时的公网 IP。"""
        answers = iter([[PUBLIC_IP], ["10.9.9.9"]])

        def flaky_resolver(host, port):
            return next(answers)

        calls = {"n": 0}

        def resolver(host, port):
            calls["n"] += 1
            return flaky_resolver(host, port)

        transport = MockTransport({
            "http://rebind.example/": (200, {}, b"ok"),
        })
        result = safe_fetch("http://rebind.example/",
                            resolver=resolver, transport=transport)
        self.assertTrue(result.ok)
        self.assertEqual(calls["n"], 1, "校验与连接之间不得再次解析")
        self.assertEqual(transport.requests[0][1], PUBLIC_IP,
                         "连接必须钉扎到校验时的 IP")


class TestDnsTimeout(unittest.TestCase):
    def test_slow_resolver_fails_closed(self):
        def slow(host, port):
            time.sleep(2)
            return [PUBLIC_IP]

        started = time.perf_counter()
        r = validate_url("http://public.example/", resolver=slow, timeout=0.3)
        elapsed = time.perf_counter() - started
        self.assertFalse(r.ok)
        self.assertIn("超时", r.reason)
        self.assertLess(elapsed, 1.0, "超时必须及时返回，不得阻塞到解析完成")


class TestTiming(unittest.TestCase):
    """校验耗时数据（打印到 stdout，供报告引用）。"""

    def test_timing(self):
        n = 3000
        resolver = make_resolver()

        t0 = time.perf_counter()
        for _ in range(n):
            validate_url("http://192.168.1.1/", resolver=resolver)
        literal_ms = (time.perf_counter() - t0) / n * 1000

        t0 = time.perf_counter()
        for _ in range(n):
            validate_url("http://public.example/", resolver=resolver)
        dns_ms = (time.perf_counter() - t0) / n * 1000

        t0 = time.perf_counter()
        for _ in range(n):
            validate_url("http://internal.example/", resolver=resolver)
        dns_block_ms = (time.perf_counter() - t0) / n * 1000

        print(f"\n[耗时] IP 字面量判定   : {literal_ms * 1000:8.2f} µs/次 (n={n})")
        print(f"[耗时] 域名放行(mockDNS): {dns_ms * 1000:8.2f} µs/次 (n={n})")
        print(f"[耗时] 域名拦截(mockDNS): {dns_block_ms * 1000:8.2f} µs/次 (n={n})")


class TestHelpers(unittest.TestCase):
    def test_parse_special_forms(self):
        cases = {
            "2130706433": "127.0.0.1",
            "0x7f000001": "127.0.0.1",
            "0177.0.0.1": "127.0.0.1",
            "127.1": "127.0.0.1",
            "0": "0.0.0.0",
            "127.0.0.1.": "127.0.0.1",
        }
        for text, expect in cases.items():
            with self.subTest(text=text):
                self.assertEqual(str(parse_ip_literal(text)), expect)

    def test_blocked_reason_public_is_none(self):
        import ipaddress
        self.assertIsNone(blocked_reason(ipaddress.ip_address(PUBLIC_IP)))
        self.assertIsNone(blocked_reason(ipaddress.ip_address(PUBLIC_V6)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
