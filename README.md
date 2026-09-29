# GSB9-27 — 控制流图分析、支配树构造与 SSA 转换

纯 Python 3 标准库实现：从三地址码（TAC）构建控制流图（CFG），标记不可达块，
构造支配树（idom）与支配边界（DF），并在此基础上构造单赋值形式（SSA）：
按迭代支配边界在汇合点插入 phi 节点、变量重命名，附双解释器对拍验证。

## 目录结构

```
cfgdom/            库
  tac.py           TAC 解析（标签 / 赋值 / if-goto / goto / ret）
  cfg.py           基本块划分（leader 算法）、连边、不可达块标记
  dom.py           支配集（迭代数据流）、idom（Cooper-Harvey-Kennedy）、
                   支配树、支配边界（Cytron 算法，迭代后序遍历）
  brute.py         暴力参考实现（删点可达性），用于对拍
  expr.py          表达式解析 / 安全求值 / 变量重命名（ast 白名单子集）
  ssa.py           SSA 构造：phi 插入（迭代 DF 工作表算法）、重命名
                   （支配树 DFS + 版本栈）、可读输出、两类校验器
  interp.py        双解释器：原始 TAC 与 SSA 形式共用同一表达式求值器
tests/
  test_structures.py  结构用例集（8 个）
  fuzz_diff.py        随机图对拍脚本
  test_ssa.py         SSA 结构用例集（7 个，精确断言 phi 位置）
  fuzz_ssa_interp.py  随机程序优化前后解释器对拍
bench.py           上万块规模耗时基准
examples_demo.py   用法示例
ssa_demo.py        SSA 构造演示（phi 位置逐项核对 + 对拍真实输出）
```

## TAC 文法

```
L1:                 标签
x = a + b           任意非控制语句
if a < b goto L2    条件跳转（后继：L2 与顺序下一条）
goto L3             无条件跳转
ret x               返回（出口）；文件末尾未终止时隐式出口
# 注释
```

## 运行命令

```bash
python3 examples_demo.py            # 用法示例
python3 tests/test_structures.py    # 结构用例集（单块/直线/菱形/嵌套循环/多出口/自环/不可达）
python3 tests/fuzz_diff.py 1000 1   # 随机图对拍：1000 轮，种子 1
python3 ssa_demo.py                 # SSA 演示：phi 位置核对 + 重命名 + 解释器对拍
python3 tests/test_ssa.py           # SSA 结构用例集（phi 位置精确断言 + def-use 校验）
python3 tests/fuzz_ssa_interp.py 1000 7   # 随机程序对拍：TAC 解释器 vs SSA 解释器
python3 bench.py                    # 万块规模耗时基准
```

## SSA 构造

- **phi 插入**：变量 v 的 phi 位置 = 其定义点集合的迭代支配边界 DF+
  （Cytron 工作表算法）；DF+ 中只可能出现汇合点（可达前驱 ≥ 2）。
- **重命名**：支配树 DFS + 每变量版本栈。v 的第 k 个定义命名为 `v_k`；
  `v_0` 为隐式入口版本，表示程序输入（未定义即引用的变量）。
- **phi 参数**：按边记录 `phi(v_i@B前驱, ...)`，与可达前驱一一对应。

## SSA 正确性保证

- `verify_phi_placement`：独立复算每个变量的迭代支配边界，逐变量核对
  phi 位置；测试中同时用快速 DF 与暴力 DF（`df_brute`）双重核对，
  并断言 phi 只落在汇合点。
- `verify_ssa`：每个版本恰好定义一次；每次使用的版本均有定义；
  定义支配使用（phi 参数按“定义支配该边前驱”判定）；phi 参数与
  可达前驱一一对应。
- 解释器对拍：随机生成保证终止的结构化程序（赋值 / if-else / 计数循环），
  同一输入下 `run_tac` 与 `run_ssa` 返回值必须一致；
  3 个种子 × 300–1000 轮全部通过（单轮 phi 总数约 1.2 万）。

## 正确性保证

- 支配集与暴力可达性算法（删点后检查可达性）逐块对拍；
  支配边界按定义 `DF(x) = { y | x 支配 y 的某前驱且 x 不严格支配 y }` 暴力对拍。
- idom 链展开必须等于支配集（每个块都校验）。
- 对拍覆盖：3 个种子 × 1000 轮随机图（1–25 块），全部通过；
  其中约 78% 的用例含不可达块、57% 含自环、5% 为多出口。
- 不可达块只出现在 `CFG.unreachable`，不进入任何支配计算（每次对拍均断言）。
- 多出口：支配分析不依赖唯一出口，天然支持；自环：`DF-local` 含 `s == n` 分支，
  入口块带回边时入口正确落入自身 DF。

## 耗时数据（Python 3.12，本机实测）

| 用例 | 块数 | 建图 | 支配树(idom) | 支配边界(DF) | 总计 |
|---|---|---|---|---|---|
| 长链 2000 块（含支配集） | 2000 | 0.005s | 0.001s | 0.002s | 0.109s（支配集 0.101s） |
| 长链 | 10000 | 0.028s | 0.005s | 0.008s | 0.041s |
| 长链 | 20000 | 0.054s | 0.010s | 0.024s | 0.087s |
| 随机图（含回边/自环） | 10000 | 0.025s | 0.016s | 0.021s | 0.062s |
| 随机图（含回边/自环） | 20000 | 0.071s | 0.046s | 0.092s | 0.209s |
| 嵌套循环 100 层 × 100 块 | 10002 | 0.020s | 0.009s | 0.010s | 0.040s |
| 嵌套循环 150 层 × 100 块 | 15002 | 0.041s | 0.016s | 0.016s | 0.074s |

注：完整支配集需要 Θ(n²) 存储，万块规模仅用于对拍验证；实际 SSA 构造管线
（建图 → 支配树 → 支配边界）在 2 万块规模下总计约 0.1–0.2s。

## API

```python
from cfgdom import (build_cfg, dominators, dominator_tree,
                    dominance_frontier, dom_brute, df_brute,
                    to_ssa, verify_phi_placement, verify_ssa,
                    run_tac, run_ssa)

cfg = build_cfg(tac_source)   # cfg.blocks / cfg.reachable_ids / cfg.unreachable
dom = dominators(cfg)         # {块号: 支配集}，仅可达块
idom, children = dominator_tree(cfg)
df = dominance_frontier(cfg)  # {块号: 支配边界}，仅可达块

prog = to_ssa(tac_source)     # SSAProgram：prog.to_text() 输出可读 SSA
verify_phi_placement(prog, cfg)   # 核对 phi 位置 == 迭代支配边界
verify_ssa(prog, cfg)             # 核对定义唯一 / 使用有定义 / 定义支配使用
run_tac(tac_source, {"n": 100})   # 优化前解释执行
run_ssa(prog, {"n": 100})         # 优化后解释执行（结果应一致）
```
