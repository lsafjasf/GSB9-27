# 数据格式版本迁移说明

格式共三个版本，升级链逐版本推进：`v1 -> v2 -> v3`。
引擎实现见 `dataformat/migrate.py`，版本定义见 `dataformat/schema.py`。

## 核心契约

1. **缺失 ≠ 默认值**
   - 必填字段缺失：抛 `MissingFieldError`，显式失败，绝不用默认值掩盖。
   - 可选字段缺失：填充默认值，并把点分路径记入结果 `_meta.defaulted`，
     下游可据此区分「用户没配」与「用户配了恰好等于默认值的值」。
2. **未知字段原样保留**：迁移只触碰 schema 声明的 key，其余一律不动，
   写回时自然带回（升级、降级均如此）。
3. **删除字段归档**：`removes` 的字段存入 `_meta.removed`，降级时可恢复。

## 升级对照表 v1 -> v2

| v1 字段       | v2 字段        | 规则                                   |
| ------------- | -------------- | -------------------------------------- |
| `name`        | `name`         | 保留                                   |
| `timeout`     | `timeout_ms`   | 改名，值 ×1000（秒 → 毫秒）            |
| `server.host` | `server.host`  | 保留（必填，缺失抛错）                 |
| `server.port` | `server.port`  | 保留（可选，缺失填 80 并标记 defaulted）|
| —             | `server.retries` | 新增嵌套字段，缺失填 3 并标记 defaulted |
| 未知字段      | 同名           | 原样保留                               |

## 升级对照表 v2 -> v3

| v2 字段       | v3 字段       | 规则                                          |
| ------------- | ------------- | --------------------------------------------- |
| `name`        | —             | 删除，值归档到 `_meta.removed.name`           |
| `timeout_ms`  | `timeout_ms`  | 保留（必填，缺失抛错）                        |
| `server.*`    | `server.*`    | 保留                                          |
| —             | `tags`        | 新增，缺失填 `[]`（工厂生成，不共享引用）并标记 |
| 未知字段      | 同名          | 原样保留                                      |

跨版本 `v1 -> v3` 即依次应用上两表。

## 降级（新版本数据被旧代码读取）

旧代码**不能直接**读新版本数据：`timeout_ms`/`tags` 它不认识，
`name` 在 v3 已不存在。必须先调用 `downgrade(data, from_v, to_v)`。

| 降级        | 规则                                                                 |
| ----------- | -------------------------------------------------------------------- |
| `v3 -> v2`  | `name` 从 `_meta.removed` 恢复；归档缺失且必填 → `MissingFieldError`；`tags` 对 v2 是未知字段，原样保留 |
| `v2 -> v1`  | `timeout_ms` → `timeout`，值 ÷1000（整除得 int，否则 float）；`server.retries` 对 v1 是未知字段，原样保留 |

注意：

- 降级结果中属于新版本的字段（如 `tags`、`server.retries`）按未知字段策略
  **保留而非丢弃**，因此「降级 → 再升级」往返不丢数据。
- 往返不保证严格相等：v1 原文档降级回来后可能多出 `server.retries` 等
  后续版本的字段，这是有意行为。
- 若 v3 数据不是经本引擎升级而来（无 `_meta.removed` 归档），降级到
  v2/v1 时 `name` 无法恢复，会显式抛 `MissingFieldError`，不会编造假值。

## 运行

```bash
python3 repro_bug.py                      # 复现旧版缺陷（修复前行为）
python3 -m unittest discover -s tests -v  # 回归 + 兼容性 + 报告/往返测试（33 个用例）
```

## 迁移报告（可逐条核对）

`dataformat/report.py` 提供报告生成与独立核对：

- `build_report(input_data, from_v, to_v, result=None)`：从 (输入, 迁移结果)
  派生逐字段报告，每条记录动作与来源：
  - `renamed`：改名/转换（来源路径、目标路径、转换函数名、前后值）；
  - `removed`：删除（值归档到 `_meta.removed`，降级可恢复）；
  - `defaulted`：缺失被 schema 默认值填充（来源标记为 `schema_default`，
    与用户显式配置严格区分）；
  - `kept` / `preserved_unknown`：已知字段保留 / 未知字段原样保留；
  - `version_bump`：版本号推进。
- `verify_report(report, input_data, result)`：独立于生成逻辑，对照输入与
  迁移结果逐条复核，并做完备性检查（输入/结果的每个叶子字段都必须被
  恰好一条报告条目覆盖），任何不一致抛 `ReportMismatchError`。
  因此报告不是「迁移过程的自述」，而是可被第三方核对的断言集合。

## 回退（降级）工具与往返一致性

命令行工具 `migrate_tool.py`：

```bash
python3 migrate_tool.py upgrade   in.json --from 1 --to 3 -o out.json --report report.json
python3 migrate_tool.py downgrade out.json --from 3 --to 1 -o back.json
python3 migrate_tool.py roundtrip in.json --from 1 --via 3
```

`roundtrip` 子命令（及 `roundtrip_diff()` API）执行 升级 -> 降级 往返，
列出往返结果与原文档的全部差异。

**往返是超集，不是严格相等。** 以 v1 -> v3 -> v1 为例，往返结果比原文档多出：

| 多出字段         | 来源                                   |
| ---------------- | -------------------------------------- |
| `server.port`    | v1 起就有的可选默认值（80）            |
| `server.retries` | v2 新增的可选默认值（3）               |
| `tags`           | v3 新增的可选默认值（`[]`）            |

原因：这些字段在升级时由默认值填充，降级时对旧版本是「未知字段」，
按未知字段策略**保留而非丢弃**——这是有意行为，保证降级不丢数据、
再升级时用户已生效的值不被默认值覆盖。差异只会是「多出」（added），
不会出现原字段被改动或丢失；`roundtrip_diff` 的测试断言了这一点。

反向地，若严格相等是硬需求，可在降级后按上表显式剔除这些字段，
但代价是丢失「用户后来显式配置过这些字段」的信息。

## 可复跑验证

```bash
python3 verify_roundtrip.py
```

端到端执行：升级并生成报告 -> 逐条核对报告 -> 降级回退 -> 往返差异断言，
真实输出（迁移结果、报告 JSON、降级结果、差异列表）写入 `out/` 目录，
可逐条人工核对。任何一步失败即非零退出。
