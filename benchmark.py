"""Propagation overhead benchmark (standard library only)."""

import time

from tracelib import HEADER_NAME, TraceContext

SECRET = b"bench-secret"
N = 20000


def bench(fn, n=N):
    fn()  # warmup
    start = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - start) / n * 1e6  # us per op


def main() -> None:
    root = TraceContext.root(SECRET, sampled=True)
    carrier = root.inject({})
    raw = carrier[HEADER_NAME]

    def full_hop():
        # child -> inject -> extract -> child: one queue/process boundary
        TraceContext.extract(root.child().inject({}), SECRET).child()

    rows = [
        ("root()        ", bench(lambda: TraceContext.root(SECRET, sampled=True))),
        ("child()       ", bench(lambda: root.child())),
        ("serialize()   ", bench(lambda: root.serialize())),
        ("deserialize() ", bench(lambda: TraceContext.deserialize(raw, SECRET))),
        ("inject()      ", bench(lambda: root.inject({}))),
        ("extract()     ", bench(lambda: TraceContext.extract(carrier, SECRET))),
        ("full hop      ", bench(full_hop)),
    ]
    print(f"iterations per measurement: {N}")
    print(f"serialized context size:    {len(raw)} bytes  ({raw[:24]}...)")
    for name, us in rows:
        print(f"{name}: {us:8.3f} us/op  ({1e6 / us:12,.0f} ops/s)")


if __name__ == "__main__":
    main()
