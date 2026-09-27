"""规模基准：元素个数 n 与状态数、内存上界、耗时的关系。

同一批随机实例上同时跑位掩码 DP 与穷举（穷举只在小规模启用）。
运行：python3 benchmark.py
"""

from __future__ import annotations

import random
import time
import tracemalloc

from bitmask_dp import memory_bound_bytes, solve, state_count
from brute_force import solve_bruteforce


def _make_instance(n, m, rng):
    items = []
    for _ in range(n):
        v = rng.randint(1, 100)
        w = [rng.randint(1, 20) for _ in range(m)]
        items.append((v, w if m > 1 else w[0]))
    # 容量约为总消耗的一半，制造有紧有松的可行域
    caps = [max(1, sum(it[1][d] if isinstance(it[1], list) else it[1]
                      for it in items) // 2) for d in range(m)]
    return items, caps if m > 1 else caps[0]


def _time(fn, repeat=1):
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def main() -> None:
    rng = random.Random(7)
    budget_1gib = 1024 * 1024 * 1024

    print("== 位掩码 DP vs 穷举：规模—耗时关系（m=1，值/重量随机，容量=总重1/2） ==")
    print(f"{'n':>3} {'状态数2^n':>12} {'内存上界MiB':>12} "
          f"{'DP耗时(s)':>12} {'穷举耗时(s)':>12}  最优值一致")
    dp_rows = {}
    for n in [10, 12, 14, 16, 18, 20, 22, 24]:
        items, cap = _make_instance(n, 1, rng)
        states = state_count(n)
        mem_mib = memory_bound_bytes(n, 1) / 2**20
        reps = 3 if n <= 16 else 1
        dp_t = _time(lambda: solve(items, cap, mem_budget_bytes=budget_1gib), reps)
        dp_rows[n] = (items, cap)
        if n <= 18:
            bf_t = _time(lambda: solve_bruteforce(items, cap))
            same = solve(items, cap, mem_budget_bytes=budget_1gib).value \
                == solve_bruteforce(items, cap).value
            bf_s = f"{bf_t:12.4f}"
            same_s = "是" if same else "否"
        else:
            bf_s = f"{'--(太慢)':>12}"
            same_s = "--"
        print(f"{n:>3} {states:>12,} {mem_mib:>12.2f} {dp_t:>12.4f} {bf_s}  {same_s}")

    print()
    print("== 约束维数 m 的影响（n=20） ==")
    items, _ = dp_rows[20]
    for m in [1, 2, 3]:
        its, cap = _make_instance(20, m, random.Random(100 + m))
        t = _time(lambda: solve(its, cap, mem_budget_bytes=budget_1gib))
        print(f"m={m}: 内存上界={memory_bound_bytes(20, m)/2**20:6.2f} MiB, "
              f"DP 耗时={t:.4f}s")

    print()
    print("== tracemalloc 实测峰值（与理论上界对照，m=1） ==")
    for n in [16, 20]:
        its, cap = _make_instance(n, 1, random.Random(5))
        tracemalloc.start()
        solve(its, cap, mem_budget_bytes=budget_1gib)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(f"n={n}: 理论上界={memory_bound_bytes(n,1)/2**20:7.2f} MiB, "
              f"tracemalloc 峰值={peak/2**20:7.2f} MiB")

    print()
    print("== 默认预算 256 MiB 下的拒绝边界（m=1,2） ==")
    for m in [1, 2]:
        limit_n = 0
        for n in range(0, 30):
            if memory_bound_bytes(n, m) <= 256 * 2**20:
                limit_n = n
        print(f"m={m}: 默认 256MiB 预算允许 n <= {limit_n} "
              f"(2^{limit_n} 状态, 需 {memory_bound_bytes(limit_n, m)/2**20:.0f} MiB); "
              f"n={limit_n+1} 起直接拒绝")


if __name__ == "__main__":
    main()
