"""SSA 结构用例集：phi 位置逐个核对、重命名 def-use 校验、解释器对拍。

每个用例：
  1. 精确断言每个变量的 phi 插入块（与迭代支配边界一致）；
  2. verify_phi_placement：用暴力支配边界 df_brute 独立复算核对；
  3. verify_ssa：定义唯一、使用有定义、定义支配使用、phi 参数对边一一对应；
  4. run_tac 与 run_ssa 在同一输入下结果一致。

运行：python3 tests/test_ssa.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cfgdom import (
    build_cfg,
    df_brute,
    dom_brute,
    run_ssa,
    run_tac,
    to_ssa_cfg,
    verify_phi_placement,
    verify_ssa,
)

CASES = {}


def case(name, expect_phis, inputs_list):
    """expect_phis: {变量: phi 所在块集合}；inputs_list: 多组输入对拍。"""
    def deco(fn):
        CASES[name] = (fn, expect_phis, inputs_list)
        return fn
    return deco


@case("菱形分支", {"y": {3}}, [{"x": 1}, {"x": 5}])
def diamond():
    return """
entry:
  if x < 2 goto L1
  y = 2
  goto L2
L1:
  y = 3
L2:
  ret y
"""


@case("循环累加", {"i": {1}, "s": {1}}, [{"n": 0}, {"n": 1}, {"n": 100}])
def loop_sum():
    return """
entry:
  i = 0
  s = 0
Loop:
  s = s + i
  i = i + 1
  if i < n goto Loop
  ret s
"""


@case("单侧定义也插phi", {"y": {2}}, [{"x": 1}, {"x": 9}])
def one_sided_def():
    # y 只在 then 分支定义，但 then 的支配边界含汇合点 => 仍需 phi
    return """
entry:
  y = 7
  if x > 5 goto L1
  y = x + 1
L1:
  ret y
"""


@case("嵌套循环", {"i": {1}, "j": {1, 2}, "s": {1, 2}}, [{"n": 3}, {"n": 10}])
def nested_loops():
    return """
entry:
  i = 0
  s = 0
Outer:
  j = 0
Inner:
  s = s + j
  j = j + 1
  if j < 3 goto Inner
  i = i + 1
  if i < n goto Outer
  ret s
"""


@case("多出口", {"y": {5}}, [{"x": -1}, {"x": 0}, {"x": 4}])
def multi_exit():
    return """
entry:
  if x < 0 goto Neg
  if x > 0 goto Pos
  y = 0
  goto Out
Neg:
  y = 0 - 1
  goto Out
Pos:
  y = 1
Out:
  ret y
"""


@case("循环continue", {"i": {1}, "s": {1}}, [{"n": 10}, {"n": 57}])
def loop_continue():
    return """
entry:
  i = 0
  s = 0
Head:
  i = i + 1
  if i > n goto Exit
  if i % 2 == 0 goto Head
  s = s + i
  goto Head
Exit:
  ret s
"""


@case("不可达块不参与SSA", {}, [{"x": 3}])
def unreachable_excluded():
    return """
entry:
  y = x * 2
  ret y
Dead:
  z = y + 1
  goto Dead
"""


def check(name, fn, expect_phis, inputs_list):
    src = fn()
    cfg = build_cfg(src)
    prog = to_ssa_cfg(cfg)

    # 1. 精确断言 phi 位置
    got = {v: set(bs) for v, bs in prog.placement.items()}
    assert got == expect_phis, (
        f"[{name}] phi 位置不符: 期望 {expect_phis}, 实际 {got}")

    # 2. 用暴力支配边界独立核对 phi 位置
    bdf = df_brute(cfg, dom_brute(cfg))
    verify_phi_placement(prog, cfg, df=bdf)
    verify_phi_placement(prog, cfg)  # 也用快速 DF 核对

    # 3. SSA 不变式
    verify_ssa(prog, cfg)

    # 4. 解释器对拍
    for inputs in inputs_list:
        r1 = run_tac(src, inputs)
        r2 = run_ssa(prog, inputs)
        assert r1 == r2, f"[{name}] 输入 {inputs}: TAC={r1} SSA={r2}"

    n_phi = sum(len(b.phis) for b in prog.blocks.values())
    print(f"  [OK] {name}: {len(cfg.blocks)} 块, phi {n_phi} 个, "
          f"{len(inputs_list)} 组输入对拍一致")


def main():
    for name, (fn, expect_phis, inputs_list) in CASES.items():
        check(name, fn, expect_phis, inputs_list)
    print(f"全部 {len(CASES)} 个 SSA 结构用例通过")


if __name__ == "__main__":
    main()
