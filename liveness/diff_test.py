"""对拍：worklist 求解器 vs 朴素逐点迭代求解器。

在大量随机 CFG（含循环、异常边、不可达块、自环、空块）上，
断言每个基本块的 live_in / live_out 完全一致。
退出码非 0 表示发现不一致。
"""

import random
import sys

from cfggen import random_cfg, gen_chain, gen_nested_loops
from liveness import solve_liveness_naive, solve_liveness_worklist


def check(blocks, tag):
    r_fast = solve_liveness_worklist(blocks)
    r_slow = solve_liveness_naive(blocks)
    if not r_fast.same_sets(r_slow):
        for b in blocks:
            if (r_fast.live_in[b] != r_slow.live_in[b]
                    or r_fast.live_out[b] != r_slow.live_out[b]):
                print("MISMATCH [%s] block %s" % (tag, b))
                print("  fast: in=%s out=%s"
                      % (sorted(r_fast.live_in[b]), sorted(r_fast.live_out[b])))
                print("  slow: in=%s out=%s"
                      % (sorted(r_slow.live_in[b]), sorted(r_slow.live_out[b])))
        return False
    return True


def main():
    n_cases = 0
    n_bad = 0

    # 1) 随机图对拍：多组参数 × 多个种子
    param_sets = [
        dict(n_vars=8,  fwd_prob=0.05, back_prob=0.03, exc_prob=0.05),
        dict(n_vars=12, fwd_prob=0.08, back_prob=0.06, exc_prob=0.10),
        dict(n_vars=20, fwd_prob=0.03, back_prob=0.08, exc_prob=0.15),
        dict(n_vars=6,  fwd_prob=0.12, back_prob=0.12, exc_prob=0.02),
    ]
    for pi, params in enumerate(param_sets):
        for seed in range(150):
            rng = random.Random(1000 * pi + seed)
            n = rng.randint(0, 60)  # 含空图与单块
            blocks = random_cfg(rng, n, **params)
            n_cases += 1
            if not check(blocks, "random p%d seed%d n%d" % (pi, seed, n)):
                n_bad += 1

    # 2) 结构化图对拍
    for n in (1, 2, 7, 100):
        n_cases += 1
        if not check(gen_chain(n), "chain%d" % n):
            n_bad += 1
    for depth, body in ((1, 1), (2, 3), (10, 5), (40, 8)):
        for exc in (True, False):
            n_cases += 1
            if not check(gen_nested_loops(depth, body, with_exceptions=exc),
                         "loops d%d b%d exc=%s" % (depth, body, exc)):
                n_bad += 1

    print("diff_test: %d cases, %d mismatches" % (n_cases, n_bad))
    sys.exit(1 if n_bad else 0)


if __name__ == "__main__":
    main()
