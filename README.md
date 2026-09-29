# GSB9-27 控制流图分析与支配树构造

从三地址码（TAC）构建控制流图（CFG），标记不可达块，并构造支配树
（idom）与支配边界（DF）。纯 Python 3 标准库，无第三方依赖。

## 目录结构

- `dom/cfg.py` — TAC 解析、基本块切分、CFG 构建、可达/不可达块标记
- `dom/dom.py` — 支配分析：RPO、idom（Cooper-Harvey-Kennedy 迭代算法）、
  支配集合、支配树、支配边界（Cytron 算法）；只处理可达块
- `dom/brute.py` — 暴力参考实现：删点后做可达性判定，O(V·(V+E))
- `cases/` — 结构用例集（单块、直线、菱形、嵌套循环、多出口+自环+不可达、
  入口回边+解析容错）
- `tests/test_structures.py` — 结构用例断言（含与暴力实现的对拍）
- `tests/stress.py` — 随机图对拍脚本
- `bench.py` — 大规模图构建/分析耗时基准

## TAC 格式

```
<label>:
  <dst> = <expr>
  if <cond> goto <L1> else goto <L2>
  goto <L>
  return
```

`#` 起注释；基本块在标号处和控制转移后开始，末尾无转移时顺序落入下一块。

## 运行命令

```sh
python3 tests/test_structures.py      # 结构用例（5 类结构 + 暴力对拍）
python3 tests/stress.py [N] [seed]    # 随机图对拍，默认 2000 张图
python3 bench.py [n_diamonds]         # 性能基准，默认 2500（10001 块）
python3 bench.py 2500 --dom-sets      # 额外物化完整支配集合（O(n^2) 输出）
```

## 正确性验证

- 快速算法（CHK）算出的每个块的支配集合、idom，与暴力可达性参考
  实现逐一对比：3 个种子 × 共 8000 张随机图（1–24 块，含自环、多出口、
  不可达块）全部一致。
- 支配边界对拍：快速算法（Cytron）的 DF 与按定义（`b ∈ DF[a]` 当且仅当
  `a` 支配 `b` 的某个前驱、且不严格支配 `b`）求得的参考 DF 集合相等。
- 不可达块单独列出于 `CFG.unreachable_names()`，不参与 idom/DF 计算
  （断言 `set(idom) == reachable`）。

## 耗时数据（CPython 3，本机实测）

图为菱形链 + 每 8 个菱形一条回边（嵌套循环），4 块/菱形：

| 块数   | 边数   | 解析+建图 | idom (CHK) | 支配树+DF | 总计     |
|--------|--------|-----------|------------|-----------|----------|
| 10001  | 12812  | 27.3 ms   | 10.0 ms    | 4.8 ms    | 42.1 ms  |
| 20001  | 25624  | 83.3 ms   | 41.2 ms    | 15.9 ms   | 140.4 ms |
| 50001  | 64062  | 257.9 ms  | 136.9 ms   | 69.1 ms   | 463.9 ms |

完整支配集合的物化输出本身是 O(n²)（10001 块约 1.7 s），默认不跑，
用 `--dom-sets` 开启；SSA 构造只需 idom + DF，即上表中的线性部分。
