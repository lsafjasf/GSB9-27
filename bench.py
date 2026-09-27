"""耗时基准：上万块规模下 CFG 构建与支配分析的耗时。

说明：完整支配集（dominators()）本身需要 Θ(n²) 存储，万块规模仅用于
正确性对拍；实际编译器管线（SSA 构造）只依赖 支配树 + 支配边界，
因此基准对这两项单独计时，支配集只在 2000 块规模给出参考数据。

运行：python3 bench.py
"""

import random
import sys
import time

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cfgdom import build_cfg, dominators, dominator_tree, dominance_frontier


def gen_chain(n: int) -> str:
    """n 块长链（深支配树）。"""
    lines = []
    for i in range(n):
        lines.append(f"B{i}:")
        lines.append(f"  t = {i}")
        lines.append(f"  goto B{i+1}" if i + 1 < n else "  ret t")
    return "\n".join(lines)


def gen_random(n: int, seed: int = 1) -> str:
    """n 块随机图（条件跳为主，含回边与自环）。"""
    rng = random.Random(seed)
    lines = []
    for i in range(n):
        lines.append(f"B{i}:")
        lines.append(f"  t = {i}")
        if i + 1 == n:
            lines.append("  ret t")
            continue
        r = rng.random()
        if r < 0.7:
            lines.append(f"  if t < 5 goto B{rng.randrange(n)}")  # 允许回边/自环
        elif r < 0.9:
            lines.append(f"  goto B{i+1}")
        else:
            lines.append("  ret t")
    return "\n".join(lines)


def gen_nested_loops(depth: int, body: int) -> str:
    """depth 层嵌套循环，每层 body 个块，总计 depth*body+2 块。"""
    lines = ["B0:", "  i = 0"]
    bid = 1
    for d in range(depth):
        head = bid
        for k in range(body):
            lines.append(f"B{bid}:")
            lines.append("  i = i + 1" if k == 0 else "  j = i * 2")
            bid += 1
            if k + 1 < body:
                lines.append(f"  goto B{bid}")  # 显式 goto 保证每段独立成块
            else:
                # 循环尾：条件跳回本层循环头，顺序下落进入下一层
                lines.append(f"  if i < 10 goto B{head}")
    lines.append(f"B{bid}:")
    lines.append("  ret i")
    return "\n".join(lines)


def bench(name: str, src: str, with_dom_sets: bool = False):
    t0 = time.perf_counter()
    cfg = build_cfg(src)
    t1 = time.perf_counter()
    idom, children = dominator_tree(cfg)
    t2 = time.perf_counter()
    df = dominance_frontier(cfg)
    t3 = time.perf_counter()
    dom_t = ""
    if with_dom_sets:
        dominators(cfg)
        t4 = time.perf_counter()
        dom_t = f" 支配集={t4-t3:7.3f}s"
    n = len(cfg.blocks)
    print(f"{name:<26} 块数={n:>6} 可达={len(cfg.reachable_ids):>6} | "
          f"建图={t1-t0:6.3f}s 支配树={t2-t1:6.3f}s 支配边界={t3-t2:6.3f}s"
          f"{dom_t} 总计={time.perf_counter()-t0:6.3f}s")


def main():
    bench("长链 2000 块(含支配集)", gen_chain(2_000), with_dom_sets=True)
    bench("长链 10000 块", gen_chain(10_000))
    bench("长链 20000 块", gen_chain(20_000))
    bench("随机图 10000 块", gen_random(10_000))
    bench("随机图 20000 块", gen_random(20_000))
    bench("嵌套循环 100层x100块", gen_nested_loops(100, 100))
    bench("嵌套循环 150层x100块", gen_nested_loops(150, 100))


if __name__ == "__main__":
    main()
