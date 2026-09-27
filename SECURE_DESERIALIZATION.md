# 安全反序列化：修复说明与使用手册

## 问题

旧实现（`vulnerable_deserializer.py`，仅作对照基线保留）的白名单只校验根节点，
且信任 payload 自带的别名/构造器，导致四类绕过：

| 用例 | 手法 | 旧实现结果 |
| --- | --- | --- |
| `nested_smuggling` | 嵌套结构里夹带未授权类型 | 绕过，构造 `Backdoor` |
| `alias_pointer` | payload 自带 `$aliases`，别名指向未授权类型 | 绕过 |
| `custom_constructor` | 白名单类型 + payload 指定 `$constructor` | 绕过 |
| `container_indirect` | 经白名单容器 `TypedList` 间接构造 | 绕过 |

检出证据见 `python3 bypass_cases.py` 的输出：旧实现全部 `BYPASSED`，
新实现全部 `REJECTED` 并给出 JSON 路径（如 `$.payload.$type`、
`$.$state.items[1].$type`）。

## 修复设计（`secure_deserializer.py`）

- **两阶段**：Phase 1 递归校验整棵 payload 树（嵌套类型、别名解析、构造器
  策略、字段白名单、`$id`/`$ref` 图、深度/节点数/字节上限），全部通过后
  Phase 2 才构造任何对象。
- **默认拒绝**：只有白名单显式登记的类型可构造；别名只存在于服务端配置，
  且别名目标本身也必须在白名单内。
- **payload 无权指定行为**：typed node 只允许 `$type/$state/$args/$id`，
  `$constructor`、`$aliases` 及其他 `$` 保留键一律拒绝。
- **构造器来自登记**：`constructor` 是白名单里登记的、注册类上的
  classmethod 名，绝不取自 payload。
- **拒绝即定位**：`DeserializationError.path` 给出出错节点的 JSON 路径。
- **资源上限**：`max_depth`（默认 64）、`max_nodes`（默认 10 万）、
  `max_bytes`（默认 100 万）；循环引用与前向引用直接拒绝。

## 白名单配置（`whitelist.json`）

```json
{
  "types": {
    "demoapp.models.User": {
      "constructor": "from_dict",
      "fields": {"name": "str", "age": "int"}
    }
  },
  "aliases": {
    "User": "demoapp.models.User"
  }
}
```

### 新增类型必须显式登记

例如要放行 `demoapp.models.Order`，在 `types` 中追加（未登记一律拒绝）：

```json
"demoapp.models.Order": {
  "constructor": null,
  "fields": {"order_id": "str", "amount": "number", "buyer": "any"}
}
```

- `constructor`：可选，注册类上的 classmethod 名（如 `from_dict`），
  省略时用 `Cls(**state)`。
- `fields`：可选，`$state` 允许的字段及类型
  （`str`/`int`/`number`/`bool`/`any`）；省略则不限制字段。
- `aliases`：可选短名映射，目标必须已登记在 `types` 中。

## 使用

```python
from secure_deserializer import SecureDeserializer, Whitelist

deserializer = SecureDeserializer(
    Whitelist.from_json_file("whitelist.json"),
    max_depth=64, max_nodes=100_000, max_bytes=1_000_000,
)
obj = deserializer.loads(payload_text)  # 违规抛 DeserializationError（含 path）
```

## 运行命令

```bash
python3 bypass_cases.py                          # 绕过用例集 + 检出证据
python3 -m unittest test_secure_deserializer -v  # 回归测试（24 项）
```
