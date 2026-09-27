"""收敛轮数与耗时基准：上千基本块规模。

指标：
- naive:   整轮扫描轮数（rounds）与传递函数求值次数（updates）
- worklist: 传递函数求值次数（updates，即块出队重算次数）
两者均报告 wall-clock 耗时，并校验结果一致。
"""

import random
import time

from cfggen import count_edges, gen_chain, gen_nested_loops, random_cfg
from liveness import solve_liveness_naive, solve_liveness_worklist


def reversed_order(blocks):
    """构造逆 id 序的同名图（dict 保持插入序）。\n    对后向分析，id 降序让后继先被计算，是好序；升序是坏序。"""
    return dict(reversed(list(blocks.items())))


def bench(name, blocks):
    t0 = time.perf_counter()
    r_slow = solve_liveness_naive(blocks)
    t1 = time.perf_counter()
    r_fast = solve_liveness_worklist(blocks)
    t2 = time.perf_counter()
    ok = r_fast.same_sets(r_slow)
    n, e = len(blocks), count_edges(blocks)
    print("%-34s n=%5d e=%6d | naive: %4d 轮 %8d 次求值 %7.1f ms | "
          "worklist: %8d 次求值 %7.1f ms | 一致=%s"
          % (name, n, e, r_slow.rounds, r_slow.updates, (t1 - t0) * 1e3,
             r_fast.updates, (t2 - t1) * 1e3, ok))
    return ok


def main():
    all_ok = True
    all_ok &= bench("chain-5000 naive坏序(升序)", gen_chain(5000))
    all_ok &= bench("nested-loops d50 b40 (2000块)",
                    gen_nested_loops(50, 40))
    all_ok &= bench("nested-loops d100 b20 无异常边",
                    gen_nested_loops(100, 20, with_exceptions=False))
    all_ok &= bench("nested-loops d50 b40 naive好序(降序)",
                    reversed_order(gen_nested_loops(50, 40)))
    all_ok &= bench("chain-5000 naive好序(降序)",
                    reversed_order(gen_chain(5000)))
    rng = random.Random(42)
    all_ok &= bench("random-2000 (循环+异常+不可达)",
                    random_cfg(rng, 2000, n_vars=24, fwd_prob=0.004,
                               back_prob=0.003, exc_prob=0.05))
    rng = random.Random(7)
    all_ok &= bench("random-4000 高密度回边",
                    random_cfg(rng, 4000, n_vars=32, fwd_prob=0.002,
                               back_prob=0.004, exc_prob=0.03))
    print("ALL CONSISTENT" if all_ok else "INCONSISTENCY FOUND")


if __name__ == "__main__":
    main()
