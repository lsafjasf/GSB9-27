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
python3 -m unittest discover -s tests -v  # 回归 + 兼容性测试（18 个用例）
```
