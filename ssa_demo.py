"""SSA 构造演示：支配边界 → phi 插入 → 重命名 → 解释器对拍。

运行：python3 ssa_demo.py
"""

from cfgdom import (
    build_cfg,
    df_brute,
    dom_brute,
    dominance_frontier,
    iterated_dominance_frontier,
    run_ssa,
    run_tac,
    to_ssa_cfg,
    verify_phi_placement,
    verify_ssa,
)

DIAMOND = """
entry:
  price = 100
  if x > 10 goto Big
  fee = price * 2
  goto Out
Big:
  fee = price + 50
Out:
  total = fee + x
  ret total
"""

LOOP_SUM = """
entry:
  i = 0
  s = 0
Loop:
  s = s + i
  i = i + 1
  if i < n goto Loop
  ret s
"""


def demo(title, src, inputs_list):
    print("=" * 64)
    print(f"【{title}】原始程序:")
    print(src.strip("\n"))
    cfg = build_cfg(src)
    prog = to_ssa_cfg(cfg)

    print("\n-- 支配边界（逐块）--")
    df = dominance_frontier(cfg)
    for bid in sorted(df):
        print(f"  DF(B{bid}) = {sorted(df[bid])}")

    print("\n-- phi 插入位置核对（定义点 → 迭代支配边界 → phi）--")
    for v in sorted(prog.defsites):
        sites = sorted(prog.defsites[v])
        expect = iterated_dominance_frontier(df, prog.defsites[v])
        got = sorted(prog.placement.get(v, set()))
        ok = "✓" if expect == set(got) else "✗"
        print(f"  {v}: 定义点 {sites} -> DF+ {sorted(expect)} -> phi {got} {ok}")
    verify_phi_placement(prog, cfg)                       # 快速 DF 核对
    verify_phi_placement(prog, cfg, df=df_brute(cfg, dom_brute(cfg)))  # 暴力 DF 核对
    print("  verify_phi_placement: 通过（快速DF 与 暴力DF 双重核对）")

    verify_ssa(prog, cfg)
    n_defs = sum(len(b.phis) + len(b.instrs) for b in prog.blocks.values())
    print(f"  verify_ssa: 通过（{n_defs} 个版本定义唯一，使用均被定义且被其定义支配）")

    print("\n-- SSA 形式 --")
    print(prog.to_text())

    print("\n-- 解释器对拍（优化前 TAC vs SSA）--")
    for inputs in inputs_list:
        r1 = run_tac(src, inputs)
        r2 = run_ssa(prog, inputs)
        ok = "✓" if r1 == r2 else "✗"
        print(f"  输入 {inputs}: TAC={r1}  SSA={r2}  {ok}")
        assert r1 == r2
    print()


def main():
    demo("菱形分支", DIAMOND, [{"x": 3}, {"x": 20}])
    demo("循环累加 0+1+...+(n-1)", LOOP_SUM, [{"n": 5}, {"n": 100}])


if __name__ == "__main__":
    main()
