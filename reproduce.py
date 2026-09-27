"""复现「总空闲内存很多却分配不出连续块」。

用法: python3 reproduce.py

步骤：
1. 在朴素池上跑一段确定性的分配/释放序列，每 500 步打印碎片率
   （最大可用连续块 / 总空闲），观察其随时间下降；
2. 序列结束后尝试一次 8KiB 分配 —— 总空闲远超 8KiB，但分配失败；
3. 同一序列在修复后的池上重跑，碎片率保持高位，同样的分配成功。
"""

import random
import sys

from src.mempool_naive import NaivePool, OutOfMemoryError as NaiveOOM
from src.mempool_fixed import MemoryPool, OutOfMemoryError as FixedOOM

CAPACITY = 1 << 20  # 1 MiB
OPS = 40000
SAMPLE_EVERY = 4000
TARGET_LIVE = 210  # 稳态存活块数，让池占用率维持在 ~50-60%
PROBE = 65536  # 复现用的大块分配：总空闲远超它，但朴素池分不出来

SMALL_SIZES = [24, 40, 72, 130, 260, 520, 1030, 2100, 4100]
BIG_SIZES = [12000, 24000]  # 服务里偶发的大对象
BIG_PROB = 0.05


def make_sequence():
    """确定性负载：大小混合的申请/释放，维持稳态占用（模拟长期运行的服务）。"""
    rng = random.Random(20260928)
    ops = []
    live = 0
    for _ in range(OPS):
        if live >= TARGET_LIVE or (live and rng.random() < 0.3):
            ops.append(("free", rng.randrange(live)))
            live -= 1
        else:
            sizes = BIG_SIZES if rng.random() < BIG_PROB else SMALL_SIZES
            ops.append(("alloc", rng.choice(sizes)))
            live += 1
    return ops


def run(pool, oom_exc, label):
    ops = make_sequence()
    live = []
    failures = 0
    print(f"\n=== {label} ===")
    print(f"{'op':>6} {'total_free':>12} {'max_block':>12} {'free_blocks':>12} {'frag':>8}")
    for step, (kind, arg) in enumerate(ops, 1):
        if kind == "alloc":
            try:
                live.append(pool.alloc(arg))
            except oom_exc:
                failures += 1
        else:
            if live:
                blk = live.pop(arg % len(live))
                blk.free()
        if step % SAMPLE_EVERY == 0:
            s = pool.stats()
            print(
                f"{step:>6} {s.total_free:>12} {s.max_block:>12} "
                f"{s.free_blocks:>12} {s.fragmentation:>8.4f}"
            )
    s = pool.stats()
    print(f"final: {s!r}  alloc_failures={failures}")

    # 关键复现：总空闲很多，但要一块 64KiB 连续内存
    NEED = PROBE
    try:
        blk = pool.alloc(NEED)
        print(
            f"alloc({NEED}) 成功 (offset={blk.offset})，"
            f"总空闲={s.total_free}"
        )
        return True
    except oom_exc as e:
        print(
            f"alloc({NEED}) 失败: {e}\n"
            f"  -> 总空闲={s.total_free} 字节，最大连续块={s.max_block} 字节，"
            f"空闲块数={s.free_blocks}，碎片率={s.fragmentation:.4f}"
        )
        return False


def main():
    naive_ok = run(NaivePool(CAPACITY), NaiveOOM, "修复前 NaivePool")
    fixed_ok = run(MemoryPool(CAPACITY), FixedOOM, "修复后 MemoryPool")
    print("\n=== 结论 ===")
    print(f"修复前 {PROBE}B 分配: {'成功' if naive_ok else '失败（复现了碎片问题）'}")
    print(f"修复后 {PROBE}B 分配: {'成功' if fixed_ok else '失败'}")
    if naive_ok or not fixed_ok:
        print("结果不符合预期", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
