"""带宽对拍：同一请求序列下，条件请求 vs 朴素全量响应的线上字节数。

运行：python3 bandwidth_demo.py
"""

from __future__ import annotations

import random

from conditional_cache import CachingClient, ResourceStore


def run_workload(seed: int = 42, resources: int = 50, steps: int = 20_000,
                 write_ratio: float = 0.05) -> tuple[int, int, dict]:
    rng = random.Random(seed)
    store = ResourceStore()
    keys = [f"res-{i}" for i in range(resources)]
    for key in keys:  # 资源体 1KB~8KB
        store.write(key, rng.randbytes(rng.randint(1024, 8192)))

    conditional = CachingClient(store, conditional=True)
    naive = CachingClient(store, conditional=False)
    stats = {"reads": 0, "writes": 0, "not_modified": 0}

    for _ in range(steps):
        key = rng.choice(keys)
        if rng.random() < write_ratio:
            store.write(key, rng.randbytes(rng.randint(1024, 8192)))
            stats["writes"] += 1
        else:
            before = conditional.bytes_received
            got = conditional.fetch(key)
            want = naive.fetch(key)
            assert got == want, "条件请求与全量响应内容不一致！"
            # 304 的线上字节远小于实体
            if conditional.bytes_received - before < 200:
                stats["not_modified"] += 1
            stats["reads"] += 1

    return naive.bytes_received, conditional.bytes_received, stats


def main() -> None:
    naive, conditional, stats = run_workload()
    saved = naive - conditional
    ratio = saved / naive * 100
    print(f"请求序列      : {stats['reads']} 次读 / {stats['writes']} 次写")
    print(f"命中 304      : {stats['not_modified']} 次 ({stats['not_modified']/stats['reads']*100:.1f}% 的读)")
    print(f"朴素全量响应  : {naive:>12,} 字节")
    print(f"条件请求      : {conditional:>12,} 字节")
    print(f"节省带宽      : {saved:>12,} 字节 ({ratio:.1f}%)")


if __name__ == "__main__":
    main()
