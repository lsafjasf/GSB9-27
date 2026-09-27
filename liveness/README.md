# 活跃变量分析（Liveness Analysis）

纯 Python 3 标准库实现。基于控制流图的后向数据流分析，迭代到不动点，
输出每个基本块的 `live_in` / `live_out` 集合，供寄存器分配与死代码消除使用。

## 文件

- `liveness.py` — 库：`CFG`/`Block` 数据结构 + 两个求解器
  - `analyze(cfg)`：工作表（worklist）算法，生产用
  - `analyze_naive(cfg)`：朴素逐点迭代，用于对拍
- `test_liveness.py` — 11 个手写边界用例（含精确期望活跃集合）
- `fuzz_liveness.py` — 随机 CFG 生成器 + 两个实现对拍
- `bench_liveness.py` — 结构用例 + 大规模收敛轮数/耗时基准

## 运行命令

```sh
cd liveness
python3 test_liveness.py        # 边界用例自测
python3 fuzz_liveness.py 1000   # 对拍：1000 张随机图（可自选种子作为第 2 参数）
python3 bench_liveness.py       # 收敛轮数与耗时
```

## 方法说明

**数据流方程**（gen/kill，并集汇合，后向）：

```
live_out[b] = ∪_{s ∈ succ(b)} live_in[s]
live_in[b]  = uses[b] ∪ (live_out[b] − defs[b])
```

**异常边**：对活跃分析而言，异常跳转边与普通边语义相同——异常处理器入口
活跃的变量在抛出点必须活跃。因此所有边等权参与 `live_out` 的并集，边的
kind（`normal`/`exceptional`）仅作为元数据保留，用于构造含异常跳转的测试图。

**迭代顺序**：工作表按「反转 CFG 的逆后序（RPO）」初始化——即从出口块出发、
沿前驱边做 DFS 得到的逆后序。该顺序保证块通常排在其后继之后，后向信息
每趟传播最远，是后向问题收敛最快的经典顺序。不可达块与无出口环通过
「未访问块补充为 DFS 根」全部纳入顺序与初始工作表。

**收敛依据**：变量集有限，其幂集格有限；两个求解器均从空集出发且活跃集
只增不减（单调转移函数 + 并集汇合），构成上升链，必在有限步内稳定。
工作表不变式：每当 `live_in[b]` 增大，b 的所有前驱重新入队；工作表为空时
所有块（含不可达块）的方程全部成立，即到达不动点。

**对拍**：`fuzz_liveness.py` 在含循环、不可达块、异常边的随机 CFG 上逐块
比较两个实现的 `live_in`/`live_out`，必须完全一致。已验证 3 个种子共
2000 张随机图全部一致。

## 收敛轮数与耗时（CPython 3.12，单次运行）

| 用例 | 块数 | worklist 求值次数（折整趟） | worklist 耗时 | 朴素迭代趟数 | 朴素耗时 |
|---|---|---|---|---|---|
| single-block | 1 | 1 (1) | 0.01 ms | 2 | 0.00 ms |
| empty-graph | 0 | 0 (0) | 0.00 ms | 0 | 0.00 ms |
| forward-only-dag | 2000 | 2000 (1) | ~6 ms | 16 | ~50 ms |
| nested-loops d200 b8 | 409 | 880 (3) | ~2 ms | 14 | ~9 ms |
| nested-loops d500 b4 | 1005 | 2239 (3) | ~5 ms | 16 | ~26 ms |
| nested-loops d1000 b2 | 2003 | 4576 (3) | ~12 ms | 17 | ~77 ms |
| random（含环/不可达/异常边） | 1000 | 1588 (2) | ~4 ms | 6 | ~10 ms |
| random | 2000 | 3206 (2) | ~10 ms | 7 | ~31 ms |
| random | 4000 | 6327 (2) | ~27 ms | 8 | ~66 ms |

观察：worklist 在纯前向 DAG 上恰好 1 趟收敛（RPO 顺序最优）；嵌套循环
深度从 200 增到 1000，worklist 仍只需约 3 趟等效扫描，而朴素迭代趟数
随规模增长；4000 块随机图 worklist 约 2 趟、27 ms 收敛。

## 边界用例覆盖（test_liveness.py）

单块、空图、纯前向链、菱形汇合、自循环、循环内 kill、双重嵌套循环、
不可达块（含不可达自循环）、异常跳转边、指向未声明块的边、无变量图。
