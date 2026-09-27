"""传播开销基准：python3 bench/overhead.py [iterations]"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracectx import deserialize, inject, extract, serialize, start_root_span, start_span

KEY = b"bench-secret-key"


def bench(fn, n):
    # 预热
    for _ in range(min(1000, n)):
        fn()
    start = time.perf_counter_ns()
    for _ in range(n):
        fn()
    elapsed = time.perf_counter_ns() - start
    return elapsed / n  # ns/op


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    root = start_root_span("bench", sampled=True)
    child = start_span("bench-child", root)
    raw_signed = serialize(child.context, key=KEY)
    raw_unsigned = serialize(child.context)
    carrier = {}
    inject(child, carrier, key=KEY)

    results = [
        ("start_span (建子节点)", bench(lambda: start_span("x", root), n)),
        ("serialize 无签名", bench(lambda: serialize(child.context), n)),
        ("serialize 带HMAC签名", bench(lambda: serialize(child.context, key=KEY), n)),
        ("deserialize 无签名", bench(lambda: deserialize(raw_unsigned), n)),
        ("deserialize 验签", bench(lambda: deserialize(raw_signed, key=KEY), n)),
        ("inject (含签名)", bench(lambda: inject(child, {}, key=KEY), n)),
        ("extract (含验签)", bench(lambda: extract(carrier, key=KEY), n)),
    ]

    print(f"iterations: {n}")
    print(f"payload 大小: 无签名 {len(raw_unsigned)} B / 带签名 {len(raw_signed)} B")
    print(f"样例: {raw_signed}")
    print("-" * 52)
    for name, ns in results:
        print(f"{name:<26} {ns:>10.0f} ns/op  ({ns/1000:>7.2f} us/op)")


if __name__ == "__main__":
    main()
