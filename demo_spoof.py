"""伪造用例检出演示：python3 demo_spoof.py"""

from forwarded_chain import ForwardedChainParser

parser = ForwardedChainParser(["10.0.0.0/8", "203.0.113.0/24", "fd00::/8"])
PEER = "10.0.0.1"

cases = [
    ("正常链", "198.51.100.7, 203.0.113.10"),
    ("伪造前缀", "1.1.1.1, 8.8.8.8, 198.51.100.7, 203.0.113.10"),
    ("伪造成可信代理", "10.0.0.99, 198.51.100.7, 203.0.113.10"),
    ("夹带异常值", "198.51.100.7, garbage-token, 203.0.113.10"),
    ("全不可信对端", "1.1.1.1, 2.2.2.2"),  # peer 会临时替换演示
]

for name, header in cases:
    peer = "198.51.100.9" if name == "全不可信对端" else PEER
    r = parser.resolve(header, peer)
    proto, _ = parser.resolve_proto("https, http, https", len(r.trusted_hops),
                                    default="(直连协议)")
    naive = header.split(",")[0].strip()
    print(f"[{name}]")
    print(f"  XFF            : {header!r}  (peer={peer})")
    print(f"  真实客户端     : {r.client_ip}")
    print(f"  朴素解析(链首) : {naive}  {'<-- 被骗!' if naive != r.client_ip else '(一致)'}")
    print(f"  检出伪造       : {r.spoofed}  丢弃值: {[h.raw for h in r.dropped]}")
    print(f"  可信路径       : {[h.text for h in r.trusted_hops]}")
    print(f"  判定说明       : {r.reason}")
    print()
