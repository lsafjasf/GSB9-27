# 作用域覆盖判定表

作用域 = 以冒号分隔的分层字符串：`<action>:<resource_type>[:<resource_id>]`

## 匹配规则

| 编号 | 规则 |
|---|---|
| R1 | **段数相等**：逐段比较，授权作用域与所需作用域段数必须相等，不存在跨层级递归 |
| R2 | **通配**：某一段授权值为 `*` 时，覆盖该段所需值的任意值；通配可出现在任意段 |
| R3 | **精确**：非 `*` 段必须与所需段逐字符相等，**区分大小写** |
| R4 | **资源限定**：第三段 `resource_id` 是资源标识；相等才放行，不同 id 即拒绝 |
| R5 | **AND 语义**：一次请求声明多个所需作用域时，逐项判定，全部覆盖才放行 |

## 判定表

| # | 授权作用域 (granted) | 所需作用域 (required) | 结果 | 依据 |
|---|---|---|---|---|
| 1 | `read:invoice` | `read:invoice` | 覆盖 | R3 精确匹配 |
| 2 | `read:invoice` | `write:invoice` | 不覆盖 | R3 action 不同 |
| 3 | `read:invoice` | `read:user` | 不覆盖 | R3 资源类型不同 |
| 4 | `read:*` | `read:invoice` | 覆盖 | R2 资源类型通配 |
| 5 | `read:*` | `write:invoice` | 不覆盖 | R2/R3 通配不跨段，非通配段 action 仍须相等 |
| 6 | `*:invoice` | `read:invoice` | 覆盖 | R2 action 通配 |
| 7 | `*:*` | `read:invoice` | 覆盖 | R2 两段均为通配 |
| 8 | `read:invoice:inv-1001` | `read:invoice:inv-1001` | 覆盖 | R4 资源 id 精确相等 |
| 9 | `read:invoice:inv-1001` | `read:invoice:inv-1002` | **不覆盖** | R4 资源不匹配 |
| 10 | `read:invoice:*` | `read:invoice:inv-1001` | 覆盖 | R2 资源 id 通配 |
| 11 | `read:invoice:*` | `read:invoice` | 不覆盖 | R1 3 段不覆盖 2 段 |
| 12 | `read:invoice` | `read:invoice:inv-1001` | 不覆盖 | R1 2 段不覆盖 3 段 |
| 13 | `read:*` | `read:invoice:inv-1001` | 不覆盖 | R1 通配不递归，段数必须相等 |
| 14 | `READ:invoice` | `read:invoice` | 不覆盖 | R3 区分大小写 |
| 15 | （多项）`read:invoice` | `read:invoice` + `write:invoice` | 拒绝 | R5 缺 `write:invoice`，AND 语义 |

判定函数为 `scope_check.covers(granted, required)`；多项判定为
`scope_check.missing_scopes(granted_list, required_list)`，返回未被覆盖项。

> 设计说明：`*` 只在自己所在的段内通配，不是“递归通配”。要表达
> “可读写所有发票”，应授予 `read:invoice:*` + `write:invoice:*`，
> 而非依赖 `read:*` 覆盖第三段。

## 资源标识如何纳入判定

请求鉴权时把目标资源 id 拼进所需作用域：

```python
required = make_scope("read", "invoice", invoice_id)  # read:invoice:inv-1001
decision = registry.authorize(token.token_id, [required])
```

- 令牌持有 3 段精确作用域 → 只能访问同一个 id（表中第 8、9 行）。
- 令牌持有 2 段作用域（如 `read:invoice`，语义为该类型全部资源）
  时不能访问具体资源——本实现采取最小权限立场：段数必须相等（R1）。
  若希望“类型级授权自动覆盖单个资源”，需显式同时签发 `read:invoice`
  与 `read:invoice:*`，让模型保持无歧义。
- 令牌持有 `read:invoice:*` → 覆盖任意 id（表中第 10 行）。
