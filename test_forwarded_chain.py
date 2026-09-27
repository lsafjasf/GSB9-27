"""forwarded_chain 自测（标准库 unittest）。

运行：python3 -m unittest -v test_forwarded_chain
"""

import unittest

from forwarded_chain import ForwardedChainParser, parse_token

# 拓扑：client -> CDN(203.0.113.10) -> LB(10.0.0.1) -> 本服务
TRUSTED = ["10.0.0.0/8", "203.0.113.0/24", "fd00::/8"]
PEER = "10.0.0.1"  # 直接 TCP 对端 = LB


def make_parser():
    return ForwardedChainParser(TRUSTED)


class TokenParsingTest(unittest.TestCase):
    def test_ipv4_plain(self):
        hop = parse_token(" 192.0.2.1 ")
        self.assertTrue(hop.valid)
        self.assertEqual(hop.text, "192.0.2.1")
        self.assertIsNone(hop.port)

    def test_ipv4_with_port(self):
        hop = parse_token("192.0.2.1:51234")
        self.assertTrue(hop.valid)
        self.assertEqual(hop.text, "192.0.2.1")
        self.assertEqual(hop.port, 51234)

    def test_ipv4_bad_port(self):
        self.assertFalse(parse_token("192.0.2.1:abc").valid)
        self.assertFalse(parse_token("192.0.2.1:99999").valid)

    def test_ipv6_bare(self):
        hop = parse_token("2001:db8::1")
        self.assertTrue(hop.valid)
        self.assertEqual(hop.text, "2001:db8::1")
        self.assertIsNone(hop.port)

    def test_ipv6_bracketed_with_port(self):
        hop = parse_token("[2001:db8::1]:8443")
        self.assertTrue(hop.valid)
        self.assertEqual(hop.text, "2001:db8::1")
        self.assertEqual(hop.port, 8443)

    def test_ipv6_bracketed_no_port(self):
        hop = parse_token("[::1]")
        self.assertTrue(hop.valid)
        self.assertEqual(hop.text, "::1")

    def test_malformed(self):
        for bad in ("", "  ", "not-an-ip", "1.2.3.4.5", "[::1", "::1]x",
                    "unknown", "Unknown", "hidden"):
            self.assertFalse(parse_token(bad).valid, bad)


class TrustResolutionTest(unittest.TestCase):
    def test_empty_chain(self):
        """空链：客户端就是对端本身（对端可信但没有转发信息）。"""
        r = make_parser().resolve(None, PEER)
        self.assertIsNone(r.client_ip)
        self.assertFalse(r.spoofed)
        self.assertEqual(r.dropped, [])
        r2 = make_parser().resolve("   ", PEER)
        self.assertIsNone(r2.client_ip)

    def test_single_value(self):
        """只有一个值：客户端 -> LB -> 服务。"""
        r = make_parser().resolve("198.51.100.7", PEER)
        self.assertEqual(r.client_ip, "198.51.100.7")
        self.assertFalse(r.spoofed)
        self.assertEqual([h.text for h in r.trusted_hops], [PEER])

    def test_normal_chain(self):
        """client -> CDN -> LB -> 服务：自右向左跳过两个可信代理。"""
        r = make_parser().resolve("198.51.100.7, 203.0.113.10", PEER)
        self.assertEqual(r.client_ip, "198.51.100.7")
        self.assertEqual([h.text for h in r.trusted_hops],
                         ["203.0.113.10", PEER])
        self.assertFalse(r.spoofed)

    def test_spoofed_prefix_detected(self):
        """伪造用例：客户端自带 XFF，左侧伪造值必须被截断并记录。"""
        r = make_parser().resolve(
            "1.1.1.1, 8.8.8.8, 198.51.100.7, 203.0.113.10", PEER)
        self.assertEqual(r.client_ip, "198.51.100.7")
        self.assertTrue(r.spoofed)
        self.assertEqual([h.raw for h in r.dropped], ["1.1.1.1", "8.8.8.8"])
        # 朴素解析（取链首）会被骗，得到 1.1.1.1
        self.assertEqual(r.naive_leftmost(), "1.1.1.1")
        self.assertNotEqual(r.client_ip, r.naive_leftmost())

    def test_spoofed_trusted_looking_value(self):
        """伪造者把自己伪造成“可信代理地址”也没用：可信判定只看真实路径右段。"""
        r = make_parser().resolve("10.0.0.99, 198.51.100.7, 203.0.113.10", PEER)
        # 10.0.0.99 虽在可信网段，但它出现在真实客户端左侧 => 丢弃
        self.assertEqual(r.client_ip, "198.51.100.7")
        self.assertTrue(r.spoofed)
        self.assertEqual([h.raw for h in r.dropped], ["10.0.0.99"])

    def test_all_untrusted(self):
        """全部不可信：对端不是可信代理，整链丢弃，客户端=对端。"""
        r = make_parser().resolve("1.1.1.1, 2.2.2.2", "198.51.100.9")
        self.assertEqual(r.client_ip, "198.51.100.9")
        self.assertTrue(r.spoofed)
        self.assertEqual([h.raw for h in r.dropped], ["1.1.1.1", "2.2.2.2"])
        self.assertEqual(r.trusted_hops, [])

    def test_all_trusted_chain(self):
        """链中每个值都在可信网段：没有客户端候选，不能拿代理地址冒充客户端。"""
        r = make_parser().resolve("10.0.0.2, 203.0.113.10", PEER)
        self.assertIsNone(r.client_ip)
        self.assertFalse(r.spoofed)

    def test_malformed_in_chain(self):
        """链中夹带异常值：异常值不可信，扫描停在那里。"""
        r = make_parser().resolve(
            "198.51.100.7, garbage-token, 203.0.113.10", PEER)
        self.assertIsNone(r.client_ip)  # 候选是畸形值，取不出地址
        self.assertIn("malformed", r.reason)
        self.assertEqual([h.raw for h in r.dropped], ["198.51.100.7"])
        # 朴素解析这里碰巧拿到 198.51.100.7，但它位于不可信边界左侧，本不应采信
        self.assertEqual(r.naive_leftmost(), "198.51.100.7")

    def test_malformed_left_of_client(self):
        """异常值在真实客户端左侧：随伪造前缀一起被丢弃。"""
        r = make_parser().resolve(
            "unknown, 198.51.100.7, 203.0.113.10", PEER)
        self.assertEqual(r.client_ip, "198.51.100.7")
        self.assertTrue(r.spoofed)
        self.assertEqual([h.raw for h in r.dropped], ["unknown"])

    def test_multi_value_whitespace_and_empty_slots(self):
        """多值拆分：容忍空格与空槽位。"""
        r = make_parser().resolve(" 198.51.100.7 ,, 203.0.113.10 ,", PEER)
        self.assertEqual(r.client_ip, "198.51.100.7")
        self.assertFalse(r.spoofed)

    def test_ports_stripped(self):
        """带端口写法：端口被剥离，地址参与信任判定。"""
        r = make_parser().resolve(
            "198.51.100.7:51234, 203.0.113.10:443", PEER)
        self.assertEqual(r.client_ip, "198.51.100.7")
        self.assertEqual(r.client_port, 51234)
        self.assertEqual([h.text for h in r.trusted_hops],
                         ["203.0.113.10", PEER])

    def test_ipv6_chain(self):
        """IPv6：裸写与带端口方括号写法混合。"""
        r = make_parser().resolve(
            "2001:db8::cafe, [fd00::1]:8443", "fd00::2")
        self.assertEqual(r.client_ip, "2001:db8::cafe")
        self.assertEqual([h.text for h in r.trusted_hops], ["fd00::1", "fd00::2"])

    def test_ipv6_peer_untrusted(self):
        r = make_parser().resolve("2001:db8::1", "2001:db8::99")
        self.assertEqual(r.client_ip, "2001:db8::99")
        self.assertTrue(r.spoofed)

    def test_invalid_peer(self):
        r = make_parser().resolve("1.1.1.1", "not-an-ip")
        self.assertIsNone(r.client_ip)
        self.assertTrue(r.spoofed)


class ProtoResolutionTest(unittest.TestCase):
    def test_proto_aligned_with_trusted_hops(self):
        p = make_parser()
        r = p.resolve("198.51.100.7, 203.0.113.10", PEER)
        proto, dropped = p.resolve_proto("http, https", len(r.trusted_hops))
        self.assertEqual(proto, "http")   # 客户端侧协议
        self.assertEqual(dropped, [])

    def test_proto_spoofed_prefix(self):
        p = make_parser()
        r = p.resolve("1.1.1.1, 198.51.100.7, 203.0.113.10", PEER)
        proto, dropped = p.resolve_proto("https, http, https", len(r.trusted_hops))
        self.assertEqual(proto, "http")
        self.assertEqual(dropped, ["https"])  # 客户端伪造的前缀被截断

    def test_proto_header_missing(self):
        proto, dropped = make_parser().resolve_proto(None, 2, default="https")
        self.assertEqual(proto, "https")
        self.assertEqual(dropped, [])

    def test_proto_chain_shorter_than_trusted(self):
        proto, _ = make_parser().resolve_proto("https", 3, default="http")
        self.assertEqual(proto, "http")

    def test_proto_no_trusted_hops(self):
        """对端不可信（0 个可信跳）：协议链整体可伪造，回退默认值。"""
        proto, dropped = make_parser().resolve_proto("https, http", 0,
                                                     default="http")
        self.assertEqual(proto, "http")
        self.assertEqual(dropped, ["https", "http"])


class NaiveComparisonTest(unittest.TestCase):
    """与朴素解析（直接取 XFF 第一个值）的差异对比。"""

    NAIVE_CASES = [
        # (header, peer, 朴素结果, 本库结果)
        ("1.1.1.1, 198.51.100.7, 203.0.113.10", PEER,
         "1.1.1.1", "198.51.100.7"),                       # 伪造前缀
        ("198.51.100.7, 203.0.113.10", PEER,
         "198.51.100.7", "198.51.100.7"),                   # 无伪造时一致
        ("10.0.0.2, 203.0.113.10", PEER,
         "10.0.0.2", None),                                 # 全可信：朴素法把代理当客户端
        ("1.1.1.1, 2.2.2.2", "198.51.100.9",
         "1.1.1.1", "198.51.100.9"),                        # 对端不可信：朴素法被骗
    ]

    def test_naive_vs_trusted(self):
        p = make_parser()
        for header, peer, naive_expected, ours_expected in self.NAIVE_CASES:
            with self.subTest(header=header, peer=peer):
                r = p.resolve(header, peer)
                naive = header.split(",")[0].strip()  # 朴素解析
                self.assertEqual(naive, naive_expected)
                self.assertEqual(r.client_ip, ours_expected)


if __name__ == "__main__":
    unittest.main()
