"""三类典型负载下，修复前后碎片率对比。

用法: python3 benchmark.py

负载：
1. mixed_extreme    大小极端混合（16B ~ 48KB 同池竞争）
2. churn_same_class 频繁申请/释放同一档（512B 热点）
3. long_lived_large 长生命周期大对象 + 小对象在其间churn

每个负载用同一份确定性操作序列分别驱动 NaivePool 和 MemoryPool，
输出碎片率（最大可用连续块/总空闲）的最小值/均值/终值与分配失败次数。
"""

import random
import statistics

from src.mempool_naive import NaivePool, OutOfMemoryError as NaiveOOM
from src.mempool_fixed import MemoryPool, OutOfMemoryError as FixedOOM

CAPACITY = 1 << 20


def gen_mixed_extreme(seed=11, ops=30000, target_live=300):
    rng = random.Random(seed)
    sizes = [16, 20, 33, 64, 100, 250, 511, 1024, 3000, 8192, 20000, 48000]
    weights = [10, 10, 10, 10, 10, 10, 10, 10, 4, 2, 1, 1]  # 小块高频，大块偶发
    seq, live = [], 0
    for _ in range(ops):
        if live >= target_live or (live and rng.random() < 0.3):
            seq.append(("free", rng.randrange(live)))
            live -= 1
        else:
            seq.append(("alloc", rng.choices(sizes, weights=weights)[0]))
            live += 1
    return seq


def gen_churn_same_class(seed=22, ops=30000, target_live=300):
    rng = random.Random(seed)
    seq, live = [], 0
    for _ in range(ops):
        if live >= target_live or (live and rng.random() < 0.5):
            seq.append(("free", rng.randrange(live)))
            live -= 1
        else:
            seq.append(("alloc", 512))
            live += 1
    return seq


def gen_long_lived_large(seed=33, ops=30000, target_live=260):
    """先放入 6 个 64KiB 长命大对象，之后小对象高频churn，偶发 16KiB 中块。"""
    rng = random.Random(seed)
    seq = [("alloc", 64 * 1024) for _ in range(6)]
    live = 6
    for _ in range(ops):
        r = rng.random()
        if live >= target_live or (live > 6 and r < 0.35):
            # 长命大对象（下标 0..5）永不释放
            seq.append(("free", 6 + rng.randrange(live - 6)))
            live -= 1
        else:
            size = 16384 if r > 0.97 else rng.choice([32, 96, 200, 700, 1500])
            seq.append(("alloc", size))
            live += 1
    return seq


WORKLOADS = [
    ("mixed_extreme", gen_mixed_extreme),
    ("churn_same_class", gen_churn_same_class),
    ("long_lived_large", gen_long_lived_large),
]


def run(pool, oom_exc, seq):
    live = []
    failures = 0
    frags = []
    for i, (kind, arg) in enumerate(seq, 1):
        if kind == "alloc":
            try:
                live.append(pool.alloc(arg))
            except oom_exc:
                failures += 1
        else:
            if live:  # 分配可能因 OOM 未成功，存活数与生成序列时有偏差
                blk = live.pop(arg % len(live))
                blk.free()
        if i % 500 == 0:
            frags.append(pool.stats().fragmentation)
    s = pool.stats()
    return {
        "frag_min": min(frags),
        "frag_avg": statistics.fmean(frags),
        "frag_final": s.fragmentation,
        "failures": failures,
        "max_block": s.max_block,
        "total_free": s.total_free,
    }


def main():
    header = (
        f"{'workload':<18} {'pool':<7} {'frag_min':>9} {'frag_avg':>9} "
        f"{'frag_final':>11} {'max_block':>10} {'total_free':>11} {'fails':>6}"
    )
    print(header)
    print("-" * len(header))
    for name, gen in WORKLOADS:
        seq = gen()
        naive = run(NaivePool(CAPACITY), NaiveOOM, seq)
        fixed = run(MemoryPool(CAPACITY), FixedOOM, seq)
        for label, r in (("naive", naive), ("fixed", fixed)):
            print(
                f"{name:<18} {label:<7} {r['frag_min']:>9.4f} {r['frag_avg']:>9.4f} "
                f"{r['frag_final']:>11.4f} {r['max_block']:>10} "
                f"{r['total_free']:>11} {r['failures']:>6}"
            )
        print()


if __name__ == "__main__":
    main()
