"""结构用例集：单块、直线、菱形、深层嵌套循环、多出口、自环、不可达块。

运行：python3 tests/test_structures.py
每个用例都与暴力算法对拍支配集与支配边界。
"""

import os
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

CASES = {}

def case(name):
    def deco(fn):
        CASES[name] = fn
        return fn
    return deco


@case("单块")
def single_block():
    return """
entry:
  a = 1
  b = a + 2
  ret b
"""


@case("直线代码")
def straight_line():
    return """
entry:
  a = 1
  b = a + 1
  c = b + 1
  d = c + 1
  ret d
"""


@case("菱形分支")
def diamond():
    return """
entry:
  x = 1
  if x < 2 goto L1
  y = 2
  goto L2
L1:
  y = 3
L2:
  ret y
"""


@case("深层嵌套循环")
def nested_loops(depth=6):
    lines = ["entry:", "  i = 0"]
    for d in range(depth):
        lines.append(f"L{d}:")
        lines.append(f"  i = i + 1")
        lines.append(f"  if i < 10 goto L{d+1}" if d + 1 < depth else "  if i < 10 goto Lend")
    lines.append("Lend:")
    lines.append("  if i > 0 goto L0")
    lines.append("  ret i")
    return "\n".join(lines)


@case("多出口")
def multi_exit():
    return """
entry:
  x = 1
  if x < 0 goto Neg
  if x > 0 goto Pos
  ret 0
Neg:
  ret -1
Pos:
  ret 1
"""


@case("自环")
def self_loop():
    return """
entry:
  i = 0
Loop:
  i = i + 1
  if i < 100 goto Loop
  ret i
"""


@case("不可达块")
def unreachable():
    return """
entry:
  x = 1
  ret x
Dead1:
  y = 2
  goto Dead2
Dead2:
  z = y + 1
  goto Dead1
"""


@case("循环内多出口+continue")
def loop_multi_exit():
    return """
entry:
  i = 0
Head:
  i = i + 1
  if i > 100 goto Exit1
  if i == 50 goto Exit2
  if i % 2 == 0 goto Head
  j = i * 2
  goto Head
Exit1:
  ret i
Exit2:
  ret 50
"""


def check(name, src):
    cfg = build_cfg(src)
    dom = dominators(cfg)
    idom, children = dominator_tree(cfg)
    df = dominance_frontier(cfg)
    bdom = dom_brute(cfg)
    bdf = df_brute(cfg, bdom)

    assert dom == bdom, f"[{name}] 支配集与暴力结果不一致"
    assert df == bdf, f"[{name}] 支配边界与暴力结果不一致"

    # idom 与支配集的一致性：idom 链必须给出完整支配集
    for n in dom:
        chain = set()
        cur = n
        while True:
            chain.add(cur)
            if idom[cur] == cur:
                break
            cur = idom[cur]
        assert chain == dom[n], f"[{name}] 块 {n} 的 idom 链与支配集不符"

    # 不可达块不得出现在任何支配结果中
    dead = {b.bid for b in cfg.unreachable}
    for res in (dom, df, idom):
        assert not (dead & set(res)), f"[{name}] 不可达块混入支配计算"

    n_unreach = len(cfg.unreachable)
    print(f"  [OK] {name}: {len(cfg.blocks)} 块, "
          f"{len(cfg.reachable_ids)} 可达, {n_unreach} 不可达")


def main():
    for name, fn in CASES.items():
        check(name, fn())
    print(f"全部 {len(CASES)} 个结构用例通过")


if __name__ == "__main__":
    main()
