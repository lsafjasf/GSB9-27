# 逐版本字段对照表与降级行为说明

格式共三个版本，升级链为 v1 → v2 → v3，逐级推进。
迁移规则的唯一权威定义在 `src/dataformat.py` 的 `STEPS` 中，本表与其一一对应。

## v1 → v2

| v1 字段 | v2 字段 | 变化 | 缺失时的行为 |
|---|---|---|---|
| `name` | `name` | 保留（必填） | 抛 `MissingFieldError` |
| `nick` | `nickname` | 改名 | 抛 `MissingFieldError`（不得用 `""` 掩盖） |
| — | `email` | 新增，默认 `null` | 填默认值并记入 `defaults_applied` |
| `settings.theme` | `settings.theme` | 保留（必填） | 抛 `MissingFieldError` |
| `settings.font_size` | `settings.font_size` | 保留（必填） | 抛 `MissingFieldError` |
| — | `settings.language` | 新增，默认 `"en"` | 填默认值并记入 `defaults_applied` |
| 任意未知字段 | 同名原样保留 | 保留 | — |

## v2 → v3

| v2 字段 | v3 字段 | 变化 | 缺失时的行为 |
|---|---|---|---|
| `name` | `name` | 保留（必填） | 抛 `MissingFieldError` |
| `nickname` | `nickname` | 保留（必填） | 抛 `MissingFieldError` |
| `email` | `contact_email` | 改名 | 抛 `MissingFieldError` |
| — | `tags` | 新增，默认 `[]` | 填默认值并记入 `defaults_applied` |
| `settings.theme` | `settings.theme` | 保留（必填） | 抛 `MissingFieldError` |
| `settings.language` | `settings.language` | 保留（必填） | 抛 `MissingFieldError` |
| `settings.font_size` | — | 删除 | 移除并记入 `report.deleted` |
| — | `settings.timezone` | 新增，**无默认值（REQUIRED）** | 必须由 `fill={"settings.timezone": ...}` 显式提供，否则抛 `MissingFieldError` |
| 任意未知字段 | 同名原样保留 | 保留 | — |

## 「缺失」与「默认值」的区分

- 有默认值的新增字段：填默认值，路径同时记录在迁移报告
  `MigrationReport.defaults_applied` 和文档内 `__meta__.defaults_applied`，
  下游可据此判断"该值是默认值，不是真实数据"。
- 数据中已真实存在的值（即使恰好等于默认值）**不会**被标记。
- 无默认值的新增字段（REQUIRED）和本应存在的字段缺失时，抛出
  `MissingFieldError`，绝不用默认值掩盖。

## 降级（新版本数据被旧代码读取）

不支持把数据从新版迁移回旧版（`migrate` 会抛 `UnsupportedVersionError`），
降级场景按"旧代码直接读新数据"处理：

- **旧代码看不见的字段**：v3 新增的 `contact_email`、`tags`、
  `settings.timezone` 对 v2 代码不可见。只要旧代码通过
  `merge_reader_write` 写回（保留未知字段），这些字段不会丢失；
  旧代码尝试写入自己不认识的字段会被拒绝。
- **旧代码期望但已不存在的字段**：`email`（已改名为 `contact_email`）和
  `settings.font_size`（已删除）。旧代码必须把它们当作"缺失"显式处理，
  不得用默认值冒充。
- 用 `compatibility_report(reader_version, data_version)` 可获得机器可读
  的影响清单（`missing_for_reader` / `invisible_to_reader`），
  用于上线前的兼容性检查。
