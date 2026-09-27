"""回归测试：重定向环路检测 + 跳数兜底。

运行：python3 test_redirects.py -v
"""

import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import buggy_client
import redirect_client
from redirect_client import (
    RedirectLoopError,
    TooManyRedirectsError,
    follow_redirects,
    normalize_url,
)


def stub_fetch(table, log):
    """按 URL 查表返回 (status, headers, body)，并记录请求顺序。"""

    def fetch(url):
        log.append(url)
        status, location = table[url]
        return status, ({"location": location} if location else {}), b""

    return fetch


class ReproTests(unittest.TestCase):
    """复现原始缺陷：两地址互跳时 buggy 版只能打满跳数上限。"""

    TABLE = {
        "http://a.test/1": (302, "http://b.test/2"),
        "http://b.test/2": (302, "http://a.test/1"),
    }

    def test_buggy_client_ping_pong_until_limit(self):
        log = []
        with self.assertRaises(buggy_client.TooManyRedirectsError):
            buggy_client.follow_redirects(
                "http://a.test/1", max_hops=10, fetch=stub_fetch(self.TABLE, log)
            )
        # 缺陷表现：10 次请求全部浪费在两个地址之间来回
        self.assertEqual(len(log), 10)
        self.assertEqual(len(set(log)), 2)

    def test_fixed_client_detects_loop_immediately(self):
        log = []
        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects(
                "http://a.test/1", max_hops=10, fetch=stub_fetch(self.TABLE, log)
            )
        # 修复后：第 3 次即将请求重复地址时即检出，只发了 2 次真实请求
        self.assertEqual(len(log), 2)
        self.assertEqual(
            ctx.exception.chain,
            ["http://a.test/1", "http://b.test/2", "http://a.test/1"],
        )
        self.assertEqual(ctx.exception.first_visit_index, 0)


class LoopDetectionTests(unittest.TestCase):
    def test_three_address_cycle(self):
        table = {
            "http://x.test/a": (302, "http://y.test/b"),
            "http://y.test/b": (302, "http://z.test/c"),
            "http://z.test/c": (302, "http://x.test/a"),
        }
        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects("http://x.test/a", fetch=stub_fetch(table, []))
        self.assertEqual(
            ctx.exception.chain,
            ["http://x.test/a", "http://y.test/b", "http://z.test/c", "http://x.test/a"],
        )

    def test_relative_and_cross_protocol_mixed(self):
        table = {
            "http://Example.com:80/start": (302, "../a"),          # 相对地址
            "http://Example.com:80/a": (302, "https://example.com/b"),  # 跨协议
            "https://example.com/b": (302, "http://EXAMPLE.COM:80/start"),
        }
        log = []
        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects("http://Example.com:80/start", fetch=stub_fetch(table, log))
        # 最后一跳与第 1 跳规范化后等价（大小写 + 默认端口）
        self.assertEqual(ctx.exception.first_visit_index, 0)
        self.assertEqual(len(log), 3)
        self.assertEqual(ctx.exception.chain[-1], "http://EXAMPLE.COM:80/start")

    def test_case_and_default_port_equivalence(self):
        table = {"http://h.test/x": (302, "HTTP://H.TEST:80/x")}
        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects("http://h.test/x", fetch=stub_fetch(table, []))
        self.assertEqual(ctx.exception.first_visit_index, 0)

    def test_query_param_order_equivalence(self):
        table = {"http://h.test/p?a=1&b=2": (302, "http://h.test/p?b=2&a=1")}
        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects("http://h.test/p?a=1&b=2", fetch=stub_fetch(table, []))
        self.assertEqual(ctx.exception.first_visit_index, 0)

    def test_different_query_values_are_not_a_loop(self):
        table = {
            "http://h.test/p?n=1": (302, "http://h.test/p?n=2"),
            "http://h.test/p?n=2": (200, None),
        }
        resp = follow_redirects("http://h.test/p?n=1", fetch=stub_fetch(table, []))
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.chain, ["http://h.test/p?n=1", "http://h.test/p?n=2"])


class HopLimitTests(unittest.TestCase):
    def test_hop_limit_backstop_for_ever_growing_chain(self):
        # 地址永不重复：环路检测帮不上忙，只能靠 max_hops 截断
        def fetch(url):
            n = int(url.rsplit("=", 1)[1])
            return 302, {"location": "http://h.test/r?n=%d" % (n + 1)}, b""

        with self.assertRaises(TooManyRedirectsError) as ctx:
            follow_redirects("http://h.test/r?n=0", max_hops=5, fetch=fetch)
        self.assertEqual(ctx.exception.max_hops, 5)
        self.assertEqual(len(ctx.exception.chain), 6)


class SuccessTests(unittest.TestCase):
    def test_successful_chain_returns_response_with_chain(self):
        table = {
            "http://h.test/a": (301, "/b"),
            "http://h.test/b": (200, None),
        }
        resp = follow_redirects("http://h.test/a", fetch=stub_fetch(table, []))
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.chain, ["http://h.test/a", "http://h.test/b"])


class NormalizeTests(unittest.TestCase):
    def test_equivalent_forms_normalize_equal(self):
        base = "http://example.com/p?a=1&b=2"
        for other in (
            "HTTP://EXAMPLE.COM:80/p?b=2&a=1",
            "http://example.com/p?a=1&b=2#frag",
            "http://example.com/./p?a=1&b=2",
            "http://example.com/%70?a=1&b=2",
        ):
            self.assertEqual(normalize_url(base), normalize_url(other), other)

    def test_distinct_forms_normalize_different(self):
        base = normalize_url("http://example.com/p?a=1")
        self.assertNotEqual(base, normalize_url("https://example.com/p?a=1"))
        self.assertNotEqual(base, normalize_url("http://example.com:8080/p?a=1"))
        self.assertNotEqual(base, normalize_url("http://example.com/p?a=2"))


class _PingPongHandler(BaseHTTPRequestHandler):
    peer = None  # (host, port)

    def do_GET(self):
        host, port = self.server.server_address
        target = "http://%s:%d%s" % (self.peer[0], self.peer[1], self.path)
        self.send_response(302)
        self.send_header("Location", target)
        self.end_headers()

    def log_message(self, *args):
        pass


class EndToEndTests(unittest.TestCase):
    """真实本地双服务器互跳：复现卡死场景并验证修复。"""

    @classmethod
    def setUpClass(cls):
        cls.srv1 = HTTPServer(("127.0.0.1", 0), _PingPongHandler)
        cls.srv2 = HTTPServer(("127.0.0.1", 0), _PingPongHandler)
        cls.srv1.RequestHandlerClass = type("H1", (_PingPongHandler,), {})
        cls.srv2.RequestHandlerClass = type("H2", (_PingPongHandler,), {})
        a = cls.srv1.server_address
        b = cls.srv2.server_address
        cls.srv1.RequestHandlerClass.peer = ("127.0.0.1", b[1])
        cls.srv2.RequestHandlerClass.peer = ("127.0.0.1", a[1])
        cls.threads = [
            threading.Thread(target=s.serve_forever, daemon=True) for s in (cls.srv1, cls.srv2)
        ]
        for t in cls.threads:
            t.start()
        cls.start_url = "http://127.0.0.1:%d/ping" % a[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv1.shutdown()
        cls.srv2.shutdown()

    def test_buggy_client_hits_hop_limit_over_real_sockets(self):
        with self.assertRaises(buggy_client.TooManyRedirectsError):
            buggy_client.follow_redirects(self.start_url, max_hops=8)

    def test_fixed_client_reports_loop_with_full_chain(self):
        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects(self.start_url)
        chain = ctx.exception.chain
        self.assertEqual(chain[0], chain[-1])
        self.assertEqual(len(chain), 3)
        self.assertIn("redirect loop detected", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
