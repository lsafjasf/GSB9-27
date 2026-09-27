"""ssrf_guard 绕过用例集与自测。

运行：python3 -m unittest test_ssrf_guard -v
覆盖：内网/回环//链路本地/保留段、域名解析到内网、重定向到内网、
IPv6 与特殊数字写法、user@host 用户名欺骗、DNS 重绑定、超时解析，
并输出校验耗时统计。
"""

import statistics
import time
import unittest

from ssrf_guard import OutboundGuard, ResolutionTimeout, SsrfBlocked

PUBLIC_IP = "93.184.216.34"   # example.com 历史公网地址，仅作测试
PUBLIC_IP_2 = "93.184.216.35"

# 本地 mock 解析器：完全离线，可控地模拟“域名解析到内网”等场景
DNS_TABLE = {
    "localhost": ["127.0.0.1"],
    "public.example.com": [PUBLIC_IP],
    "api.example.com": [PUBLIC_IP],
    "sub.api.example.com": [PUBLIC_IP],
    "internal.evil.com": ["10.0.0.9"],
    "metadata.evil.com": ["169.254.169.254"],
    "dual.evil.com": [PUBLIC_IP, "192.168.1.1"],      # 一条公网一条内网
    "v6loop.evil.com": ["::1"],
    "mapped.evil.com": ["::ffff:127.0.0.1"],
    "ula.evil.com": ["fd12::1"],
    "linklocal.evil.com": ["fe80::1"],
    "cgnat.evil.com": ["100.64.0.1"],
    "to4.evil.com": ["2002:0a00:0001::1"],             # 6to4 内嵌 10.0.0.1
    "nxdomain.evil.com": [],
}


def mock_resolver(host, port):
    if host in DNS_TABLE:
        return list(DNS_TABLE[host])
    raise OSError("NXDOMAIN")


def make_guard(**kw):
    kw.setdefault("resolver", mock_resolver)
    return OutboundGuard(**kw)


# ---------------------------------------------------------------- 绕过用例集
# (用例名, URL)；全部必须被拒绝（抛 SsrfBlocked）
BYPASS_CASES = [
    # 回环 / 本机
    ("v4-loopback", "http://127.0.0.1/"),
    ("v4-loopback-high", "http://127.63.99.1/"),
    ("v4-any", "http://0.0.0.0/"),
    ("localhost", "http://localhost/"),
    ("localhost-trailing-dot", "http://localhost./"),
    # 特殊数字写法（十进制/十六进制/八进制/简写）
    ("decimal-int", "http://2130706433/"),             # 127.0.0.1
    ("hex-int", "http://0x7f000001/"),                 # 127.0.0.1
    ("hex-dotted", "http://0x7f.0.0.1/"),
    ("octal", "http://0177.0.0.1/"),                   # 127.0.0.1
    ("short-2", "http://127.1/"),
    ("short-3", "http://127.0.1/"),
    ("single-zero", "http://0/"),
    ("decimal-metadata", "http://2852039166/"),        # 169.254.169.254
    # 内网 / 链路本地 / 保留段（IPv4 字面量）
    ("rfc1918-10", "http://10.0.0.1/"),
    ("rfc1918-172", "http://172.16.0.1/"),
    ("rfc1918-172-max", "http://172.31.255.255/"),
    ("rfc1918-192", "http://192.168.0.1/"),
    ("link-local", "http://169.254.169.254/"),         # 云元数据
    ("cgnat", "http://100.64.0.1/"),
    ("benchmark", "http://198.18.0.1/"),
    ("test-net-1", "http://192.0.2.1/"),
    ("test-net-2", "http://198.51.100.1/"),
    ("test-net-3", "http://203.0.113.1/"),
    ("multicast", "http://224.0.0.1/"),
    ("reserved-240", "http://240.0.0.1/"),
    ("broadcast", "http://255.255.255.255/"),
    # IPv6
    ("v6-loopback", "http://[::1]/"),
    ("v6-unspecified", "http://[::]/"),
    ("v6-mapped-loopback", "http://[::ffff:127.0.0.1]/"),
    ("v6-mapped-hex", "http://[::ffff:7f00:1]/"),
    ("v6-mapped-private", "http://[::ffff:10.0.0.1]/"),
    ("v6-link-local", "http://[fe80::1]/"),
    ("v6-link-local-zone", "http://[fe80::1%25eth0]/"),
    ("v6-ula", "http://[fd12::1]/"),
    ("v6-multicast", "http://[ff02::1]/"),
    ("v6-nat64-private", "http://[64:ff9b::a00:1]/"),  # NAT64 内嵌 10.0.0.1
    ("v6-6to4-private", "http://[2002:0a00:1::1]/"),   # 6to4 内嵌 10.0.0.1
    ("v6-doc", "http://[2001:db8::1]/"),
    # 域名解析到内网（mock 解析器）
    ("dns-to-private", "http://internal.evil.com/"),
    ("dns-to-metadata", "http://metadata.evil.com/"),
    ("dns-mixed-answers", "http://dual.evil.com/"),
    ("dns-to-v6-loopback", "http://v6loop.evil.com/"),
    ("dns-to-v6-mapped", "http://mapped.evil.com/"),
    ("dns-to-ula", "http://ula.evil.com/"),
    ("dns-to-linklocal", "http://linklocal.evil.com/"),
    ("dns-to-cgnat", "http://cgnat.evil.com/"),
    ("dns-to-6to4", "http://to4.evil.com/"),
    ("dns-nxdomain", "http://nxdomain.evil.com/"),
    ("dns-no-record", "http://absent.evil.com/"),
    # 用户名欺骗 user@host
    ("userinfo-basic", "http://user@127.0.0.1/"),
    ("userinfo-deceive", "http://public.example.com@127.0.0.1/"),
    ("userinfo-reverse", "http://127.0.0.1@public.example.com/"),
    ("userinfo-pass", "http://user:pass@public.example.com/"),
    ("userinfo-empty", "http://@public.example.com/"),
    # 结构类绕过
    ("backslash", "http://public.example.com\\@127.0.0.1/"),
    ("scheme-ftp", "ftp://public.example.com/"),
    ("scheme-file", "file:///etc/passwd"),
    ("scheme-gopher", "gopher://public.example.com/"),
    ("port-ssh", "http://public.example.com:22/"),
    ("port-internal-svc", "http://public.example.com:6379/"),
    ("port-bad", "http://public.example.com:99999/"),
]


class BypassCorpusTest(unittest.TestCase):
    """绕过用例集：全部必须被拒绝。"""

    def test_bypass_corpus_all_rejected(self):
        guard = make_guard()
        failures = []
        for name, url in BYPASS_CASES:
            with self.subTest(case=name):
                try:
                    guard.validate_url(url)
                    failures.append(name)
                except SsrfBlocked:
                    pass
        self.assertEqual(failures, [], f"以下用例未被拦截: {failures}")

    def test_each_case_reports_reason(self):
        guard = make_guard()
        for name, url in BYPASS_CASES[:5]:
            with self.subTest(case=name):
                with self.assertRaises(SsrfBlocked) as ctx:
                    guard.validate_url(url)
                self.assertTrue(str(ctx.exception))


class AllowCaseTest(unittest.TestCase):
    """合法目标必须放行（避免过度拦截）。"""

    def test_public_host_allowed(self):
        target = make_guard().validate_url("http://public.example.com/path?q=1")
        self.assertEqual(target.ips, [PUBLIC_IP])
        self.assertEqual(target.port, 80)

    def test_https_default_port(self):
        target = make_guard().validate_url("https://public.example.com/")
        self.assertEqual(target.port, 443)

    def test_public_ip_literal_allowed(self):
        target = make_guard().validate_url(f"http://{PUBLIC_IP}/")
        self.assertEqual(target.ips, [PUBLIC_IP])


class AllowlistTest(unittest.TestCase):
    """域名白名单：精确或 .后缀 边界匹配。"""

    def setUp(self):
        self.guard = make_guard(allowed_hosts=["api.example.com"])

    def test_exact_match_allowed(self):
        self.guard.validate_url("http://api.example.com/")

    def test_subdomain_allowed(self):
        self.guard.validate_url("http://sub.api.example.com/")

    def test_not_in_allowlist_rejected(self):
        with self.assertRaises(SsrfBlocked):
            self.guard.validate_url("http://public.example.com/")

    def test_suffix_spoof_rejected(self):
        with self.assertRaises(SsrfBlocked):
            self.guard.validate_url("http://api.example.com.evil.com/")

    def test_label_boundary(self):
        # notapi.example.com 不应匹配 api.example.com
        DNS_TABLE["notapi.example.com"] = [PUBLIC_IP]
        with self.assertRaises(SsrfBlocked):
            self.guard.validate_url("http://notapi.example.com/")


class RedirectTest(unittest.TestCase):
    """重定向每一跳都重新校验。"""

    def test_redirect_to_internal_blocked(self):
        guard = make_guard()

        def http_get(url, target):
            return 302, {"location": "http://169.254.169.254/latest/meta-data"}, b""

        with self.assertRaises(SsrfBlocked):
            guard.fetch("http://public.example.com/start", http_get=http_get)

    def test_redirect_chain_to_internal_blocked(self):
        guard = make_guard()
        hops = {
            "http://public.example.com/a": "http://public.example.com/b",
            "http://public.example.com/b": "http://10.0.0.9/",
        }

        def http_get(url, target):
            return 302, {"location": hops[url]}, b""

        with self.assertRaises(SsrfBlocked):
            guard.fetch("http://public.example.com/a", http_get=http_get)

    def test_redirect_to_public_allowed(self):
        guard = make_guard()

        def http_get(url, target):
            if url.endswith("/a"):
                return 302, {"location": "/b"}, b""
            return 200, {}, b"ok"

        status, _, body = guard.fetch("http://public.example.com/a", http_get=http_get)
        self.assertEqual((status, body), (200, b"ok"))

    def test_redirect_loop_blocked(self):
        guard = make_guard()

        def http_get(url, target):
            return 302, {"location": "http://public.example.com/a"}, b""

        with self.assertRaises(SsrfBlocked):
            guard.fetch("http://public.example.com/a", http_get=http_get)


class RebindingTest(unittest.TestCase):
    """DNS 重绑定：校验与连接必须一致。"""

    def test_unstable_resolution_detected(self):
        answers = [[PUBLIC_IP], [PUBLIC_IP], ["10.0.0.9"]]  # 第三次变成内网
        state = {"i": 0}

        def flip_resolver(host, port):
            ans = answers[min(state["i"], len(answers) - 1)]
            state["i"] += 1
            return ans

        guard = make_guard(resolver=flip_resolver)
        with self.assertRaises(SsrfBlocked):
            guard.verify_stable_resolution("public.example.com", checks=3, interval=0.01)

    def test_connection_uses_validated_ip(self):
        # 第一次解析为公网（校验通过），若库二次解析会得到内网地址；
        # 正确行为：连接必须使用第一次校验过的公网 IP。
        answers = [[PUBLIC_IP], ["10.0.0.9"]]
        state = {"i": 0}

        def flip_resolver(host, port):
            ans = answers[min(state["i"], len(answers) - 1)]
            state["i"] += 1
            return ans

        connected = []

        class FakeSock:
            def close(self):
                pass

        def connector(address, timeout):
            connected.append(address)
            return FakeSock()

        guard = make_guard(resolver=flip_resolver)
        _, target = guard.open_connection(
            "http://public.example.com/", connector=connector
        )
        self.assertEqual(connected, [(PUBLIC_IP, 80)])
        self.assertEqual(target.ips, [PUBLIC_IP])


class TimeoutTest(unittest.TestCase):
    """超时解析：按失败关闭处理，且不会长时间阻塞。"""

    def test_slow_resolver_times_out(self):
        def slow_resolver(host, port):
            time.sleep(5)
            return [PUBLIC_IP]

        guard = make_guard(resolver=slow_resolver, dns_timeout=0.3)
        start = time.perf_counter()
        with self.assertRaises(ResolutionTimeout):
            guard.validate_url("http://public.example.com/")
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.0)


class TimingTest(unittest.TestCase):
    """校验耗时统计（mock 解析器，离线）。"""

    def test_timing_report(self):
        guard = make_guard()
        samples = []
        rounds = 200
        for _ in range(rounds):
            for _, url in BYPASS_CASES:
                t0 = time.perf_counter()
                try:
                    guard.validate_url(url)
                except SsrfBlocked:
                    pass
                samples.append((time.perf_counter() - t0) * 1e6)
        samples.sort()
        n = len(samples)
        report = (
            f"\n[timing] 用例数={len(BYPASS_CASES)} 轮次={rounds} 样本={n}\n"
            f"[timing] min={samples[0]:.1f}us "
            f"avg={statistics.fmean(samples):.1f}us "
            f"p50={samples[n // 2]:.1f}us "
            f"p95={samples[int(n * 0.95)]:.1f}us "
            f"max={samples[-1]:.1f}us"
        )
        print(report)
        # 回归阈值：单条校验 p95 不应超过 5ms（mock 解析器下通常在微秒级）
        self.assertLess(samples[int(n * 0.95)], 5000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
