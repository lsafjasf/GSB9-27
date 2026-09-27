"""伪造用例检出演示：朴素解析 vs 可信链解析。

运行：python3 demo.py
"""

from forwarded_chain import naive_resolve, resolve_client

TRUSTED = ("10.0.0.0/8", "192.168.0.0/16", "fd00::/8")
INGRESS = "10.0.0.1"

SCENARIOS = [
    {
        "name": "伪造源 IP（限流绕过）",
        "desc": "客户端自带 XFF: 6.6.6.6，可信入口追加真实来源",
        "peer": INGRESS,
        "xff": "6.6.6.6, 203.0.113.50",
        "xfp": None,
    },
    {
        "name": "伪造内网 IP（冒充可信代理）",
        "desc": "注入两段内网地址，企图让信任链走到伪造值上",
        "peer": INGRESS,
        "xff": "10.9.9.9, 10.8.8.8, 203.0.113.50",
        "xfp": None,
    },
    {
        "name": "伪造协议（http 伪装成 https）",
        "desc": "客户端注入 XFP: https，可信入口追加真实协议 http",
        "peer": INGRESS,
        "xff": "6.6.6.6, 203.0.113.50",
        "xfp": "https, http",
    },
    {
        "name": "直连伪造（绕过代理直接打服务）",
        "desc": "对端不在可信网段，整个转发头都是客户端伪造",
        "peer": "203.0.113.99",
        "xff": "6.6.6.6, 7.7.7.7",
        "xfp": "https",
    },
    {
        "name": "链中夹带异常值",
        "desc": "伪造段里混入非法 token，必须在不可信边界截断",
        "peer": INGRESS,
        "xff": "1.2.3.4, garbage, 10.0.0.2",
        "xfp": None,
    },
]


def main():
    print(f"可信网段: {', '.join(TRUSTED)}\n")
    for i, sc in enumerate(SCENARIOS, 1):
        r = resolve_client(sc["peer"], x_forwarded_for=sc["xff"],
                           x_forwarded_proto=sc["xfp"],
                           trusted_networks=TRUSTED)
        naive = naive_resolve(sc["peer"], x_forwarded_for=sc["xff"],
                              x_forwarded_proto=sc["xfp"])
        print(f"[{i}] {sc['name']}")
        print(f"    场景:       {sc['desc']}")
        print(f"    peer:       {sc['peer']}  XFF: {sc['xff']!r}"
              + (f"  XFP: {sc['xfp']!r}" if sc["xfp"] else ""))
        print(f"    朴素解析:   client={naive['client_ip']}"
              f" proto={naive['protocol']}   <-- 被伪造值欺骗")
        print(f"    信任链解析: client={r.client_ip} proto={r.protocol}")
        print(f"    伪造检出:   spoof_detected={r.spoof_detected}"
              f"  丢弃={[str(h) for h in r.discarded]}")
        for anomaly in r.anomalies:
            print(f"    异常记录:   {anomaly}")
        print()


if __name__ == "__main__":
    main()
