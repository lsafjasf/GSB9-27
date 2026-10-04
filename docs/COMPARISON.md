# 增量 vs 全量：对比数据

本文件由 `python3 tools/benchmark.py` 自动生成。每个场景在同一棵
改动后的代码树上分别执行增量与全量，二者报告逐字节比较；
原始报告见 `docs/comparison/<场景>_{incremental,full}.txt`。

指标定义：
- **selected**：静态推断选中的 (检查, 目标) 对数量
- **ran**：缓存键失效后实际执行的数量（≤ selected）
- **precision**：实际执行中失败的比例（1 - 空转率）
- **recall**：改动后新增失败中被增量执行覆盖的比例（1.0 = 无漏检）

## 汇总

| 场景 | 模式 | 回退 | 目标总数 | selected | ran | reused | 工作量削减 | precision | recall | 失败数 | 输出一致 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 单文件改动（被导入的叶子模块） | incremental | - | 21 | 6 | 5 | 16 | 76% | 0.20 | 1.00 | 1 | ✅ 逐字节一致 |
| S2 多文件改动（Python + 文档） | incremental | - | 21 | 6 | 5 | 16 | 76% | 0.40 | 1.00 | 2 | ✅ 逐字节一致 |
| S3a 配置文件改动（checks.conf 收紧阈值） | incremental | - | 21 | 21 | 21 | 0 | 0% | 0.05 | 1.00 | 1 | ✅ 逐字节一致 |
| S3b 配置文件改动（MANIFEST.txt） | incremental | - | 21 | 1 | 1 | 20 | 95% | 1.00 | 1.00 | 1 | ✅ 逐字节一致 |
| S4a 无法推断：未知文件类型 → 回退全量 | full | F2 | 21 | 21 | 21 | 0 | 0% | 0.00 | 1.00 | 0 | ✅ 逐字节一致 |
| S4b 无法推断：语法错误 → 回退全量 | full | F3 | 21 | 21 | 21 | 0 | 0% | 0.05 | 1.00 | 1 | ✅ 逐字节一致 |
| S5 新增模块（同步登记 MANIFEST） | incremental | - | 24 | 4 | 4 | 20 | 83% | 0.00 | 1.00 | 0 | ✅ 逐字节一致 |
| S6 删除模块（反向依赖扇出） | incremental | - | 18 | 3 | 2 | 16 | 89% | 0.50 | 1.00 | 1 | ✅ 逐字节一致 |

## S1 单文件改动（被导入的叶子模块）

pricing 被 cart/billing/checkout 导入；改动引入未解析 import，C002 沿反向依赖扇出。

```
变更: M:src/shop/pricing.py
推断映射:
  M:src/shop/pricing.py
    -> C001 src/shop/pricing.py
    -> C002 src/shop/billing.py
    -> C002 src/shop/cart.py
    -> C002 src/shop/checkout.py
    -> C002 src/shop/pricing.py
    -> C003 src/shop/pricing.py
执行: ran=5 reused=16 total=21 (76% 削减)
输出对比: 增量与全量报告逐字节一致（见 docs/comparison/S1_*.txt）
```

## S2 多文件改动（Python + 文档）

两个互不相关的文件各自命中不同检查项，计划取并集。

```
变更: M:src/shop/inventory.py, M:docs/README.md
推断映射:
  M:src/shop/inventory.py
    -> C001 src/shop/inventory.py
    -> C002 src/shop/cart.py
    -> C002 src/shop/checkout.py
    -> C002 src/shop/inventory.py
    -> C003 src/shop/inventory.py
  M:docs/README.md
    -> C005 docs/README.md
执行: ran=5 reused=16 total=21 (76% 削减)
输出对比: 增量与全量报告逐字节一致（见 docs/comparison/S2_*.txt）
```

## S3a 配置文件改动（checks.conf 收紧阈值）

90 字符行在旧阈值 100 下通过、新阈值 80 下失败；配置变更使所有消费该配置的检查缓存键失效。

```
变更: M:checks.conf, M:src/shop/inventory.py
推断映射:
  M:checks.conf
    -> C001 src/shop/__init__.py
    -> C001 src/shop/billing.py
    -> C001 src/shop/cart.py
    -> C001 src/shop/checkout.py
    -> C001 src/shop/inventory.py
    -> C001 src/shop/pricing.py
    -> C002 src/shop/__init__.py
    -> C002 src/shop/billing.py
    -> C002 src/shop/cart.py
    -> C002 src/shop/checkout.py
    -> C002 src/shop/inventory.py
    -> C002 src/shop/pricing.py
    -> C003 src/shop/__init__.py
    -> C003 src/shop/billing.py
    -> C003 src/shop/cart.py
    -> C003 src/shop/checkout.py
    -> C003 src/shop/inventory.py
    -> C003 src/shop/pricing.py
    -> C004 <repo>
    -> C005 docs/API.md
    -> C005 docs/README.md
  M:src/shop/inventory.py
    -> C001 src/shop/inventory.py
    -> C002 src/shop/cart.py
    -> C002 src/shop/checkout.py
    -> C002 src/shop/inventory.py
    -> C003 src/shop/inventory.py
执行: ran=21 reused=0 total=21 (0% 削减)
输出对比: 增量与全量报告逐字节一致（见 docs/comparison/S3a_*.txt）
```

## S3b 配置文件改动（MANIFEST.txt）

MANIFEST 只被全局检查 C004 消费，仅 1 个目标需要重跑。

```
变更: M:MANIFEST.txt
推断映射:
  M:MANIFEST.txt
    -> C004 <repo>
执行: ran=1 reused=20 total=21 (95% 削减)
输出对比: 增量与全量报告逐字节一致（见 docs/comparison/S3b_*.txt）
```

## S4a 无法推断：未知文件类型 → 回退全量

没有任何检查项认领 .txt 文件，触发 F2 回退。

```
变更: A:data/notes.txt
推断映射:
  (no per-change plan; full run)
回退原因: F2: cannot infer impact of 'data/notes.txt' (no check owns it)
执行: ran=21 reused=0 total=21 (回退全量)
输出对比: 增量与全量报告逐字节一致（见 docs/comparison/S4a_*.txt）
```

## S4b 无法推断：语法错误 → 回退全量

依赖图无法安全构建，触发 F3 回退；全量结果仍由 C001 报出。

```
变更: M:src/shop/billing.py
推断映射:
  (no per-change plan; full run)
回退原因: F3: 'src/shop/billing.py' does not parse; import graph is unsafe
执行: ran=21 reused=0 total=21 (回退全量)
输出对比: 增量与全量报告逐字节一致（见 docs/comparison/S4b_*.txt）
```

## S5 新增模块（同步登记 MANIFEST）

新文件只运行自身检查 + 树敏感的 C004。

```
变更: A:src/shop/search.py, M:MANIFEST.txt
推断映射:
  A:src/shop/search.py
    -> C001 src/shop/search.py
    -> C002 src/shop/search.py
    -> C003 src/shop/search.py
    -> C004 <repo>
  M:MANIFEST.txt
    -> C004 <repo>
执行: ran=4 reused=20 total=24 (83% 削减)
输出对比: 增量与全量报告逐字节一致（见 docs/comparison/S5_*.txt）
```

## S6 删除模块（反向依赖扇出）

删除 inventory 使 cart 的 import 悬空，C002 必须扇出到 cart。

```
变更: D:src/shop/inventory.py, M:MANIFEST.txt
推断映射:
  D:src/shop/inventory.py
    -> C002 src/shop/cart.py
    -> C002 src/shop/checkout.py
    -> C004 <repo>
  M:MANIFEST.txt
    -> C004 <repo>
执行: ran=2 reused=16 total=18 (89% 削减)
输出对比: 增量与全量报告逐字节一致（见 docs/comparison/S6_*.txt）
```

## 结论

- 全部场景增量合并输出与全量输出逐字节一致，recall = 1.0，无漏检。
- 可推断场景（S1/S2/S3b/S5/S6）工作量削减 71%–95%；配置变更（S3a）按依赖映射精确失效，无法推断时（S4a/S4b）安全回退全量。
