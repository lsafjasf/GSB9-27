"""redirect_client 回归测试。运行: python3 tests/test_redirects.py 或 python3 -m unittest -v"""
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from redirect_client import (  # noqa: E402
    RedirectLoopError,
    TooManyRedirectsError,
    follow_redirects,
    normalize_url,
)


def fake_fetch(table, calls=None):
    """table: {精确 URL: (status, headers, body)}；未命中返回 toom 404。"""
    def fetch(url):
        if calls is not None:
            calls.append(url)
        status, headers = table.get(url, (404, {}))
        return status, headers, b""
    return fetch


class NormalizeUrlTest(unittest.TestCase):
    def test_scheme_host_case_and_default_port(self):
        self.assertEqual(
            normalize_url("HTTP://Example.COM:80/Path"),
            normalize_url("http://example.com/Path"),
        )
        self.assertEqual(
            normalize_url("https://EXAMPLE.com:443/a"),
            normalize_url("https://example.com/a"),
        )
        # 非默认端口必须保留差异
        self.assertNotEqual(
            normalize_url("http://example.com:8080/a"),
            normalize_url("http://example.com/a"),
        )

    def test_query_order_and_empty_query(self):
        self.assertEqual(
            normalize_url("http://h.test/r?a=1&b=2"),
            normalize_url("http://h.test/r?b=2&a=1"),
        )
        self.assertEqual(normalize_url("http://h.test/r?"), normalize_url("http://h.test/r"))
        # 参数值不同 = 不同资源，不得误判成环
        self.assertNotEqual(
            normalize_url("http://h.test/r?a=1"),
            normalize_url("http://h.test/r?a=2"),
        )

    def test_path_dot_segments_fragment_and_root(self):
        self.assertEqual(normalize_url("http://h.test"), normalize_url("http://h.test/"))
        self.assertEqual(
            normalize_url("http://h.test/a/./b/../c"),
            normalize_url("http://h.test/a/c"),
        )
        self.assertEqual(
            normalize_url("http://h.test/p#frag"),
            normalize_url("http://h.test/p"),
        )
        self.assertEqual(
            normalize_url("http://h.test/%7Euser"),
            normalize_url("http://h.test/~user"),
        )


class LoopDetectionTest(unittest.TestCase):
    def test_two_address_mutual_loop(self):
        table = {
            "http://a.test/start": (302, {"location": "http://b.test/next"}),
            "http://b.test/next": (302, {"location": "http://a.test/start"}),
        }
        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects("http://a.test/start", fetch=fake_fetch(table))
        self.assertEqual(
            ctx.exception.chain,
            ["http://a.test/start", "http://b.test/next", "http://a.test/start"],
        )

    def test_three_address_ring(self):
        table = {
            "http://a.test/1": (301, {"location": "http://b.test/2"}),
            "http://b.test/2": (302, {"location": "http://c.test/3"}),
            "http://c.test/3": (307, {"location": "http://a.test/1"}),
        }
        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects("http://a.test/1", fetch=fake_fetch(table))
        self.assertEqual(
            ctx.exception.chain,
            ["http://a.test/1", "http://b.test/2", "http://c.test/3", "http://a.test/1"],
        )

    def test_relative_and_cross_protocol_mixed(self):
        table = {
            "http://a.test/start": (302, {"location": "/step2"}),               # 相对地址
            "http://a.test/step2": (302, {"location": "https://a.test/step3"}),  # 跨协议
            "https://a.test/step3": (302, {"location": "http://a.test/start"}),  # 跨协议回跳成环
        }
        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects("http://a.test/start", fetch=fake_fetch(table))
        self.assertEqual(
            ctx.exception.chain,
            [
                "http://a.test/start",
                "http://a.test/step2",
                "https://a.test/step3",
                "http://a.test/start",
            ],
        )

    def test_case_and_default_port_equivalence_detected(self):
        # 每次跳转的字符串都不同，但规范化后是同一地址
        table = {
            "http://example.com/": (302, {"location": "HTTP://EXAMPLE.COM:80/"}),
        }
        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects("http://example.com/", fetch=fake_fetch(table))
        self.assertEqual(len(ctx.exception.chain), 2)

    def test_query_order_equivalence_detected(self):
        table = {
            "http://h.test/r?a=1&b=2": (302, {"location": "http://h.test/r?b=2&a=1"}),
        }
        with self.assertRaises(RedirectLoopError):
            follow_redirects("http://h.test/r?a=1&b=2", fetch=fake_fetch(table))

    def test_different_query_values_not_a_loop(self):
        table = {
            "http://h.test/r?a=1": (302, {"location": "http://h.test/r?a=2"}),
            "http://h.test/r?a=2": (200, {}),
        }
        status, _headers, _body, chain = follow_redirects(
            "http://h.test/r?a=1", fetch=fake_fetch(table)
        )
        self.assertEqual(status, 200)
        self.assertEqual(chain, ["http://h.test/r?a=1", "http://h.test/r?a=2"])

    def test_max_redirects_backstop_for_non_repeating_chain(self):
        # 目标永不重复（计数器型），环路检测无法命中，靠跳数上限兜底
        def fetch(url):
            n = int(url.split("n=")[1])
            return 302, {"location": "http://h.test/r?n=%d" % (n + 1)}, b""

        with self.assertRaises(TooManyRedirectsError) as ctx:
            follow_redirects("http://h.test/r?n=0", max_redirects=5, fetch=fetch)
        self.assertEqual(len(ctx.exception.chain), 7)  # 起始 + 5 跳 + 被拦截的第 6 跳

    def test_happy_path_returns_final_response(self):
        table = {
            "http://h.test/a": (301, {"location": "/b"}),
            "http://h.test/b": (200, {}),
        }
        status, _headers, _body, chain = follow_redirects(
            "http://h.test/a", fetch=fake_fetch(table)
        )
        self.assertEqual(status, 200)
        self.assertEqual(chain, ["http://h.test/a", "http://h.test/b"])


def make_server(routes):
    """routes: {path: (status, location_or_none)}；location 为 None 时返回 200。"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, location = routes.get(self.path, (404, None))
            self.send_response(status)
            if location is not None:
                self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class LiveServerTest(unittest.TestCase):
    """端到端：真实 HTTP 服务器 + 真实网络栈（默认 fetch）。"""

    def setUp(self):
        self.servers = []

    def tearDown(self):
        for srv in self.servers:
            srv.shutdown()
            srv.server_close()

    def start(self, routes):
        srv = make_server(routes)
        self.servers.append(srv)
        host, port = srv.server_address
        return "http://%s:%d" % (host, port)

    def test_live_two_server_mutual_loop(self):
        # 两台真实服务器互跳
        routes_a = {}
        routes_b = {}
        base_a = self.start(routes_a)
        base_b = self.start(routes_b)
        routes_a["/start"] = (302, base_b + "/next")
        routes_b["/next"] = (302, base_a + "/start")

        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects(base_a + "/start", max_redirects=50)
        chain = ctx.exception.chain
        self.assertEqual(chain, [base_a + "/start", base_b + "/next", base_a + "/start"])
        self.assertLessEqual(len(chain), 3)  # 远小于上限 50，证明不是靠兜底

    def test_live_three_address_ring(self):
        routes_a, routes_b, routes_c = {}, {}, {}
        base_a = self.start(routes_a)
        base_b = self.start(routes_b)
        base_c = self.start(routes_c)
        routes_a["/1"] = (301, base_b + "/2")
        routes_b["/2"] = (302, base_c + "/3")
        routes_c["/3"] = (303, base_a + "/1")

        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects(base_a + "/1")
        self.assertEqual(
            ctx.exception.chain,
            [base_a + "/1", base_b + "/2", base_c + "/3", base_a + "/1"],
        )

    def test_live_relative_redirect_loop(self):
        routes = {"/a": (302, "/b"), "/b": (302, "./a")}
        base = self.start(routes)
        with self.assertRaises(RedirectLoopError) as ctx:
            follow_redirects(base + "/a")
        self.assertEqual(ctx.exception.chain, [base + "/a", base + "/b", base + "/a"])

    def test_live_happy_path(self):
        routes = {"/a": (301, "/b"), "/b": (200, None)}
        base = self.start(routes)
        status, _headers, _body, chain = follow_redirects(base + "/a")
        self.assertEqual(status, 200)
        self.assertEqual(chain, [base + "/a", base + "/b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
