# 依赖映射（Dependency Map）

本文件是 `precheck` 影响面推断的权威说明。映射的机器可读版本在
`precheck.json`；**本文件与 `precheck.json` 都属于 `config_files`，
任何改动都会触发全量回退**（防止映射本身被改坏后仍按旧映射增量执行）。

## 检查项 ← 文件模式

| 检查项 | 依赖的文件模式 | 说明 |
|---|---|---|
| `py-syntax` | `demo/src/**.py`, `demo/tests/**.py` | AST 解析所有 Python 源文件与测试 |
| `py-style` | `demo/src/**.py` | 行长 ≤100、无行尾空白、无 tab、文件以换行结尾 |
| `py-tests` | `demo/src/**.py`, `demo/tests/**.py` | 运行 `demo/tests` 下的 unittest |
| `json-validate` | `demo/data/**.json` | JSON 可解析性校验 |
| `config-validate` | `precheck.json` | 校验依赖映射配置本身的结构与完整性 |

## 推断规则（优先级从高到低）

1. **命中 `config_files`**（`precheck.json`、`DEPENDENCY_MAP.md`）
   → `CONFIG_CHANGED`，回退全量。
2. **命中 `ignore_patterns`**（`**.md`、`docs/**`、`.git/**`）
   → 不影响任何检查。
3. **命中一个或多个检查的 `patterns`**
   → 只执行这些检查（多文件改动取并集）。
4. **以上都不命中**
   → `UNKNOWN_IMPACT`，回退全量。
5. **配置文件本身无法加载/校验失败**
   → `CONFIG_INVALID`，回退全量（使用内置默认映射执行，
   并由 `config-validate` 报告具体错误）。

## 为什么增量与全量等价

每个检查项的输入集合 = 其 `patterns` 展开后的文件集合。改动文件集合与某
检查的输入集合无交集时，该检查的输出必然不变（检查均为纯函数：只读输入
文件，输出确定性排序）。因此：

- 增量模式只需重跑「输入集合与改动集合有交集」的检查；
- 被跳过的检查结果与改动前完全一致，无需重跑；
- `compare` 子命令对同一改动同时跑两种模式，逐检查项断言
  「状态 + 明细」完全一致（耗时字段除外）。

> 注意：增量判定成立的前提是基线（上一次全量）为绿。若基线本身有红，
> 被跳过检查的历史失败不会出现在增量报告中——发布流水线仍应保留
> 定时全量任务。

## 命中率（理论值）

| 改动类型 | 受影响检查 | 命中率 |
|---|---|---|
| 单个 `demo/src/*.py` | py-syntax, py-style, py-tests | 3/5 |
| 单个 `demo/tests/*.py` | py-syntax, py-tests | 2/5 |
| 单个 `demo/data/*.json` | json-validate | 1/5 |
| 仅文档 (`**.md`) | 无 | 0/5 |
| 配置文件 / 未知文件 | 全部（回退） | 5/5 |

实测数据见 `RESULTS.md`（由 `scripts/run_scenarios.py` 生成）。
