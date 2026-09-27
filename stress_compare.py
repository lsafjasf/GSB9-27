"""对拍 + 耗时对比脚本。

用法:
    python3 stress_compare.py            # 对拍 + 基准测试
    python3 stress_compare.py --check-only   # 只跑随机对拍
"""

import random
import sys
import time

from sliding_window_extrema import SlidingWindowExtrema
from brute_force import BruteForceExtrema


def stress(seed: int, ops: int) -> None:
    """随机插入 / 随机 resize 序列下，每一步的最值都必须与暴力一致。"""
    rng = random.Random(seed)
    fast = SlidingWindowExtrema(window=rng.choice([0, 1, 2, 3, 7, 64]))
    slow = BruteForceExtrema(window=fast.window)
    # 值域刻意取小，制造大量相等元素
    for step in range(ops):
        if rng.random() < 0.15:
            new_w = rng.choice([0, 1, 2, 3, 5, 17, 100, 10_000])
            fast.resize(new_w)
            slow.resize(new_w)
        else:
            v = rng.randint(0, 20)
            fast.push(v)
            slow.push(v)
        assert len(fast) == len(slow), (seed, step, "len")
        assert fast.min() == slow.min(), (seed, step, "min", fast.min(), slow.min())
        assert fast.max() == slow.max(), (seed, step, "max", fast.max(), slow.max())
    print(f"  seed={seed}: {ops} ops OK "
          f"(final window={fast.window}, n={fast._n})")


def bench(n: int, window: int) -> None:
    rng = random.Random(12345)
    data = [rng.random() for _ in range(n)]

    fast = SlidingWindowExtrema(window)
    t0 = time.perf_counter()
    for v in data:
        fast.push(v)
        fast.min()
        fast.max()
    t_fast = time.perf_counter() - t0

    slow = BruteForceExtrema(window)
    t0 = time.perf_counter()
    for v in data:
        slow.push(v)
        slow.min()
        slow.max()
    t_slow = time.perf_counter() - t0

    print(f"  n={n}, window={window}")
    print(f"    单调队列: {t_fast:.3f}s  ({t_fast / n * 1e6:.2f} us/op)")
    print(f"    暴力重算: {t_slow:.3f}s  ({t_slow / n * 1e6:.2f} us/op)")
    print(f"    加速比:   {t_slow / t_fast:.1f}x")


def main() -> None:
    check_only = "--check-only" in sys.argv
    print("[1/2] 随机对拍（含随机 resize / 窗口 0 / 窗口 1 / 大量相等元素）")
    for seed in range(10):
        stress(seed, ops=50_000)

    if check_only:
        return
    print("[2/2] 耗时对比（每步 push + min + max）")
    bench(n=1_000_000, window=1_000)
    bench(n=200_000, window=10_000)


if __name__ == "__main__":
    main()
