"""随机图对拍：快速算法 vs 暴力可达性算法。

随机生成 CFG（含自环、多出口、不可达块），转成 TAC 文本走完整
构建管线，然后比对：
  1. 支配集 == 暴力支配集
  2. idom 链展开 == 支配集
  3. 支配边界 == 按定义暴力计算的支配边界
  4. 不可达块不出现在任何支配结果中

运行：python3 tests/fuzz_diff.py [轮数] [种子]
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cfgdom import (
    build_cfg,
    dominators,
    dominator_tree,
    dominance_frontier,
    dom_brute,
    df_brute,
)


def random_graph_tac(rng: random.Random, n_blocks: int) -> str:
    """生成 n_blocks 个块的随机 CFG 的 TAC 文本。

    每个块随机选择终结方式：下落 / goto / 条件跳 / ret，
    目标标签随机（允许自环），从而自然产生多出口与不可达块。
    """
    labels = [f"B{i}" for i in range(n_blocks)]
    lines = []
    for i in range(n_blocks):
        lines.append(f"{labels[i]}:")
        for _ in range(rng.randint(0, 3)):
            lines.append(f"  t{rng.randint(0, 5)} = {rng.randint(0, 9)}")
        r = rng.random()
        if i == n_blocks - 1:
            kind = "ret" if r < 0.5 else "goto"
        elif r < 0.30:
            kind = "fall"
        elif r < 0.60:
            kind = "goto"
        elif r < 0.90:
            kind = "cjump"
        else:
            kind = "ret"
        if kind == "goto":
            lines.append(f"  goto {rng.choice(labels)}")
        elif kind == "cjump":
            lines.append(f"  if t0 < t1 goto {rng.choice(labels)}")
        elif kind == "ret":
            lines.append(f"  ret t0")
        # fall：什么都不加，顺序下落到下一块
    return "\n".join(lines)


def check_one(cfg, tag):
    dom = dominators(cfg)
    idom, _ = dominator_tree(cfg)
    df = dominance_frontier(cfg)
    bdom = dom_brute(cfg)
    bdf = df_brute(cfg, bdom)

    assert dom == bdom, f"{tag}: 支配集不一致"
    assert df == bdf, f"{tag}: 支配边界不一致"
    for n in dom:
        chain = set()
        cur = n
        while True:
            chain.add(cur)
            if idom[cur] == cur:
                break
            cur = idom[cur]
        assert chain == dom[n], f"{tag}: 块 {n} idom 链 != 支配集"
    dead = {b.bid for b in cfg.unreachable}
    for res in (dom, df, idom):
        assert not (dead & set(res)), f"{tag}: 不可达块混入支配计算"


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260928
    rng = random.Random(seed)
    stats = {"unreachable_cases": 0, "self_loop_cases": 0, "multi_exit": 0}
    for it in range(rounds):
        n = rng.randint(1, 25)
        src = random_graph_tac(rng, n)
        cfg = build_cfg(src)
        if cfg.unreachable:
            stats["unreachable_cases"] += 1
        if any(b.bid in b.succs for b in cfg.blocks):
            stats["self_loop_cases"] += 1
        exits = sum(1 for b in cfg.blocks if b.reachable and not b.succs)
        if exits > 1:
            stats["multi_exit"] += 1
        check_one(cfg, f"iter={it} seed={seed} n={n}")
    print(f"对拍通过: {rounds} 轮 (seed={seed})")
    print(f"  含不可达块的用例: {stats['unreachable_cases']}")
    print(f"  含自环的用例:     {stats['self_loop_cases']}")
    print(f"  多出口用例:       {stats['multi_exit']}")


if __name__ == "__main__":
    main()
