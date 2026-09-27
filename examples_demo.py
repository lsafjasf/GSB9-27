"""用法示例：从 TAC 构建 CFG，输出支配树与支配边界。

运行：python3 examples_demo.py
"""

from cfgdom import build_cfg, dominators, dominator_tree, dominance_frontier

SRC = """
entry:
  i = 0
  s = 0
Loop:
  s = s + i
  i = i + 1
  if i < 100 goto Loop
  if s > 1000 goto Big
  ret s
Big:
  ret -1
Dead:
  x = 1
  goto Dead
"""

cfg = build_cfg(SRC)
print("基本块:")
for b in cfg.blocks:
    mark = "" if b.reachable else "  [不可达]"
    print(f"  B{b.bid}: succs={b.succs} preds={b.preds}{mark}  "
          f"instrs={[i.text for i in b.instrs]}")

print("\n支配集:")
for n, ds in sorted(dominators(cfg).items()):
    print(f"  Dom(B{n}) = {sorted(ds)}")

idom, children = dominator_tree(cfg)
print("\n立即支配 idom:", dict(sorted(idom.items())))
print("支配树孩子:", {k: v for k, v in sorted(children.items())})

print("\n支配边界:")
for n, fs in sorted(dominance_frontier(cfg).items()):
    print(f"  DF(B{n}) = {sorted(fs)}")

print("\n不可达块:", [b.bid for b in cfg.unreachable])
