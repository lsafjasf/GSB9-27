# 迁移说明：键等价类变更

## 为什么需要迁移

旧实现用 `key.lower()` 做比较，新实现用
`NFKC(casefold(NFKC(key)))`（`turkic` 区域另有 I/i 预映射）。
因此**键的等价类发生了变化**：过去被判为不同的键，现在可能等价。
凡是按旧规则建立的索引、去重集合、唯一约束，都必须重建。

## 等价类的具体变化

现在会合并（旧实现判为不同）的键：

| 键 A | 键 B | 原因 |
| --- | --- | --- |
| `STRASSE` | `Straße` | casefold 全大小写折叠（ß→ss） |
| `οδος` | `οδοσ` | 希腊词尾 ς 与 σ 折叠一致 |
| `ＡＢＣ１２３` | `abc123` | NFKC 全角→半角 |
| `ｶﾀｶﾅ` | `カタカナ` | NFKC 半角片假名→全角 |
| `café` | `café` | 组合字符与预组合字符归一 |
| `µm` | `μm` | 兼容字符（微符号→希腊 μ） |
| `İSTANBUL` | `istanbul` | 仅 `turkic` 区域规则 |

反向风险（`turkic` 区域）：`I` 与 `i` 在默认规则下等价，
在 `turkic` 下**不再等价**——从默认规则切到 `turkic` 时，
原本合并的键可能拆分，唯一约束可能冲突，需人工裁决。

注意：变音符号本身仍然有意义，`café` ≠ `cafe`，不会误合并。

## 迁移步骤

1. **固定区域规则**：为每个数据集选定 `default` 或 `turkic`，
   并把该选择随数据一起持久化；同一索引内不可混用规则。
2. **重建索引/去重**：用 `canonical_key()` 重新计算所有键：

   ```python
   from keynorm import canonical_key

   def rebuild_index(records, profile="default"):
       index = {}
       collisions = {}
       for key, value in records:
           canon = canonical_key(key, profile)
           if canon in index and index[canon] != value:
               collisions.setdefault(canon, [index[canon]]).append(value)
           index[canon] = value
       return index, collisions
   ```

3. **处理冲突**：同一等价类内若原有多个不同取值（如 `STRASSE`
   和 `Straße` 各有一条记录），需要业务侧决定保留哪条；
   上面的 `collisions` 收集了全部冲突，不要静默丢弃。
4. **校验**：重建后跑 `python3 -m unittest test_keynorm` 确认行为，
   并抽查新旧键集合的差集是否符合上表预期。

## 幂等性保证

`normalize_key` 幂等（归一化结果再归一化不变），
所以规范化后的键可以安全落库，后续比较直接对存储值再跑一次
`normalize_key` 即可，无需区分"已归一化/未归一化"。
