"""forwarded_chain 自测（仅标准库 unittest）。

运行：python3 -m unittest -v
"""

import unittest

from forwarded_chain import (
    Resolution,
    naive_resolve,
    parse_forwarded,
    parse_hop,
    parse_xff,
    resolve_client,
)

TRUSTED = ("10.0.0.0/8", "192.168.0.0/16", "fd00::/8")
INGRESS = "10.0.0.1"  # 可信入口代理（TCP 直连对端）


def resolve(peer=INGRESS, **kw):
    kw.setdefault("trusted_networks", TRUSTED)
    return resolve_client(peer, **kw)


class TestParseHop(unittest.TestCase):
    def test_ipv4(self):
        hop = parse_hop("203.0.113.7")
        self.assertTrue(hop.valid)
        self.assertEqual(str(hop.ip), "203.0.113.7")
        self.assertIsNone(hop.port)

    def test_ipv4_with_port(self):
        hop = parse_hop("203.0.113.7:8080")
        self.assertTrue(hop.valid)
        self.assertEqual(str(hop.ip), "203.0.113.7")
        self.assertEqual(hop.port, 8080)

    def test_ipv6_bare(self):
        hop = parse_hop("2001:db8::1")
        self.assertTrue(hop.valid)
        self.assertEqual(str(hop.ip), "2001:db8::1")
        self.assertIsNone(hop.port)

    def test_ipv6_bare_looks_like_port_is_address(self):
        # 裸写的 ::1:8080 是合法 IPv6 地址，按地址解释而非端口
        hop = parse_hop("::1:8080")
        self.assertTrue(hop.valid)
        self.assertIsNone(hop.port)

    def test_ipv6_bracketed_with_port(self):
        hop = parse_hop("[2001:db8::1]:443")
        self.assertTrue(hop.valid)
        self.assertEqual(str(hop.ip), "2001:db8::1")
        self.assertEqual(hop.port, 443)

    def test_ipv6_bracketed_without_port(self):
        hop = parse_hop("[fe80::a]")
        self.assertTrue(hop.valid)
        self.assertIsNone(hop.port)

    def test_bad_port(self):
        self.assertFalse(parse_hop("1.2.3.4:abc").valid)
        self.assertFalse(parse_hop("1.2.3.4:99999").valid)
        self.assertFalse(parse_hop("[::1]:-1").valid)

    def test_garbage_and_placeholders(self):
        for token in ("", "   ", "unknown", "UNKNOWN", "_hidden", "not-an-ip",
                      "example.com:80", "[::1", "1.2.3.4:80:90"):
            self.assertFalse(parse_hop(token).valid, token)


class TestParseHeaders(unittest.TestCase):
    def test_xff_multi_value_split_and_strip(self):
        hops = parse_xff(" 203.0.113.1 ,[2001:db8::2]:443 ,10.0.0.2 ")
        self.assertEqual([str(h.ip) for h in hops],
                         ["203.0.113.1", "2001:db8::2", "10.0.0.2"])
        self.assertEqual(hops[1].port, 443)

    def test_forwarded_rfc7239(self):
        elements = parse_forwarded(
            'for=192.0.2.60;proto=http;by=203.0.113.43, '
            'for="[2001:db8:cafe::17]:4711";proto=https'
        )
        self.assertEqual(str(elements[0].for_hop.ip), "192.0.2.60")
        self.assertEqual(elements[0].proto, "http")
        self.assertEqual(str(elements[1].for_hop.ip), "2001:db8:cafe::17")
        self.assertEqual(elements[1].for_hop.port, 4711)
        self.assertEqual(elements[1].proto, "https")


class TestTrustChain(unittest.TestCase):
    def test_empty_chain_untrusted_peer_is_client(self):
        # 空链 + 直连客户端：客户端就是 peer
        r = resolve(peer="203.0.113.9")
        self.assertEqual(r.client_ip, "203.0.113.9")
        self.assertFalse(r.peer_trusted)
        self.assertFalse(r.spoof_detected)

    def test_empty_chain_trusted_peer_unknown_client(self):
        # 空链 + 可信代理却没给转发头：无法溯源
        r = resolve()
        self.assertIsNone(r.client_ip)
        self.assertTrue(any("未提供转发头" in a for a in r.anomalies))

    def test_single_value(self):
        r = resolve(x_forwarded_for="203.0.113.9")
        self.assertEqual(r.client_ip, "203.0.113.9")
        self.assertEqual(r.discarded, [])

    def test_single_value_with_port(self):
        r = resolve(x_forwarded_for="203.0.113.9:1234")
        self.assertEqual(r.client_ip, "203.0.113.9")
        self.assertEqual(r.client_port, 1234)

    def test_all_untrusted_rightmost_wins(self):
        # 全部不可信：最右值（可信入口亲眼所见）才是客户端
        r = resolve(x_forwarded_for="198.51.100.1, 203.0.113.2")
        self.assertEqual(r.client_ip, "203.0.113.2")
        self.assertEqual([str(h.ip) for h in r.discarded], ["198.51.100.1"])
        self.assertTrue(r.spoof_detected)

    def test_all_trusted_chain_exhausted(self):
        r = resolve(x_forwarded_for="10.0.0.5, 10.0.0.6")
        self.assertIsNone(r.client_ip)
        self.assertTrue(any("链耗尽" in a for a in r.anomalies))
        self.assertEqual(len(r.trusted_hops), 2)

    def test_multi_proxy_walk(self):
        r = resolve(x_forwarded_for="203.0.113.9, 10.0.0.5, 192.168.1.2")
        self.assertEqual(r.client_ip, "203.0.113.9")
        self.assertEqual([str(h.ip) for h in r.trusted_hops],
                         ["10.0.0.5", "192.168.1.2"])

    def test_invalid_value_in_middle_truncates(self):
        # 链中夹带异常值：在该处截断，其左侧一律丢弃
        r = resolve(x_forwarded_for="1.2.3.4, garbage, 10.0.0.2")
        self.assertIsNone(r.client_ip)
        self.assertTrue(any("非法" in a for a in r.anomalies))
        self.assertEqual([h.raw.strip() for h in r.discarded], ["1.2.3.4"])
        self.assertTrue(r.spoof_detected)

    def test_unknown_placeholder_in_middle(self):
        r = resolve(x_forwarded_for="unknown, 10.0.0.2")
        self.assertIsNone(r.client_ip)
        self.assertTrue(any("非法" in a for a in r.anomalies))

    def test_ipv6_chain(self):
        r = resolve(x_forwarded_for="[2001:db8::1]:443, fd00::8")
        self.assertEqual(r.client_ip, "2001:db8::1")
        self.assertEqual(r.client_port, 443)
        self.assertEqual([str(h.ip) for h in r.trusted_hops], ["fd00::8"])

    def test_headers_mapping_case_insensitive(self):
        r = resolve(headers={"X-Forwarded-For": "203.0.113.9",
                             "X-Forwarded-Proto": "https"})
        self.assertEqual(r.client_ip, "203.0.113.9")
        self.assertEqual(r.protocol, "https")

    def test_forwarded_header_preferred_and_proto(self):
        r = resolve(forwarded='for=203.0.113.9;proto=https, for=10.0.0.5;proto=http',
                    x_forwarded_for="6.6.6.6")
        self.assertEqual(r.source, "forwarded")
        self.assertEqual(r.client_ip, "203.0.113.9")
        self.assertEqual(r.protocol, "https")  # 客户端那一跳记录的协议

    def test_invalid_proto_ignored(self):
        r = resolve(x_forwarded_for="203.0.113.9", x_forwarded_proto="gopher")
        self.assertIsNone(r.protocol)
        self.assertTrue(any("协议值非法" in a for a in r.anomalies))


class TestSpoofDetection(unittest.TestCase):
    """伪造用例：客户端自带的转发头必须在不可信入口处被截断。"""

    def test_spoofed_xff_truncated_at_untrusted_boundary(self):
        # 客户端自带 XFF: 6.6.6.6，可信入口追加真实来源 203.0.113.50
        r = resolve(x_forwarded_for="6.6.6.6, 203.0.113.50")
        self.assertEqual(r.client_ip, "203.0.113.50")
        self.assertEqual([h.raw.strip() for h in r.discarded], ["6.6.6.6"])
        self.assertTrue(r.spoof_detected)
        # 朴素解析会被骗
        naive = naive_resolve(INGRESS, x_forwarded_for="6.6.6.6, 203.0.113.50")
        self.assertEqual(naive["client_ip"], "6.6.6.6")
        self.assertNotEqual(naive["client_ip"], r.client_ip)

    def test_spoofed_private_ip_injected(self):
        # 伪造内网地址试图冒充可信代理，同样被截断
        r = resolve(x_forwarded_for="10.9.9.9, 10.8.8.8, 203.0.113.50")
        self.assertEqual(r.client_ip, "203.0.113.50")
        self.assertEqual([h.raw.strip() for h in r.discarded],
                         ["10.9.9.9", "10.8.8.8"])
        self.assertTrue(r.spoof_detected)

    def test_header_from_untrusted_peer_fully_discarded(self):
        # 客户端直连服务（无可信代理），整个头都是伪造
        r = resolve_client("203.0.113.99",
                           x_forwarded_for="6.6.6.6, 7.7.7.7",
                           x_forwarded_proto="https",
                           trusted_networks=TRUSTED)
        self.assertEqual(r.client_ip, "203.0.113.99")
        self.assertIsNone(r.protocol)  # 伪造的 proto 不采信
        self.assertEqual(len(r.discarded), 2)
        self.assertTrue(r.spoof_detected)

    def test_spoofed_proto_overwritten_by_trusted_ingress(self):
        # 客户端注入 XFP: https，可信入口追加真实协议 http
        r = resolve(x_forwarded_for="6.6.6.6, 203.0.113.50",
                    x_forwarded_proto="https, http")
        self.assertEqual(r.protocol, "http")
        naive = naive_resolve(INGRESS, x_forwarded_proto="https, http")
        self.assertEqual(naive["protocol"], "https")  # 朴素解析被骗


class TestNaiveComparison(unittest.TestCase):
    """与朴素解析（取链首）的差异对比。"""

    SCENARIOS = [
        # (描述, peer, xff, 朴素结果, 信任链结果)
        ("伪造IP", INGRESS, "6.6.6.6, 203.0.113.50", "6.6.6.6", "203.0.113.50"),
        ("全部不可信", INGRESS, "198.51.100.1, 203.0.113.2",
         "198.51.100.1", "203.0.113.2"),
        ("夹带异常值", INGRESS, "1.2.3.4, garbage, 10.0.0.2", "1.2.3.4", None),
        ("链耗尽", INGRESS, "10.0.0.5, 10.0.0.6", "10.0.0.5", None),
        ("直连伪造", "203.0.113.99", "6.6.6.6", "6.6.6.6", "203.0.113.99"),
    ]

    def test_differences(self):
        for desc, peer, xff, naive_ip, trusted_ip in self.SCENARIOS:
            with self.subTest(desc=desc):
                naive = naive_resolve(peer, x_forwarded_for=xff)
                r = resolve_client(peer, x_forwarded_for=xff,
                                   trusted_networks=TRUSTED)
                self.assertEqual(naive["client_ip"], naive_ip)
                self.assertEqual(r.client_ip, trusted_ip)
                self.assertNotEqual(naive["client_ip"], r.client_ip)


if __name__ == "__main__":
    unittest.main()
