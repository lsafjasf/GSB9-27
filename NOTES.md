# numclose 说明：判定规则、特殊值行为、与其他实现的差异

## 判定规则

```
|a - b| <= max(rel_tol * max(|a|, |b|), abs_tol)
```

相对容差与绝对容差取**较大者**生效。当 `max(|a|, |b|)` 接近零时，
`rel_tol * max(|a|, |b|)` 趋于 0，判定**自然退化为纯绝对容差**。
因此比较接近零的值必须显式传入 `abs_tol`，否则只有完全相等才算接近。

## 可断言的代数性质

1. **自反性**：对有限值与同号无穷，`isclose(a, a)` 恒为 True（NaN 除外）。
2. **对称性**：`isclose(a, b) == isclose(b, a)`。
   `|a - b|` 与 `max(|a|, |b|)` 都关于 a、b 对称，不存在“参考值”概念。
3. **容差单调性**：`rel_tol`、`abs_tol` 任一放宽（另一个不变），
   原来判定为接近的不会变成不接近——判定右端关于两个容差单调不减。

以上性质由 `test_numclose.py` 中的 `TestAlgebraicProperties` 在覆盖
极小值（5e-324）、极大值（约 1.8e308）、跨零点、特殊值的语料上断言。

## 特殊值行为（跨平台一致）

| 输入 | 行为 |
| --- | --- |
| NaN 与任何值（含自身） | 一律不接近，返回 False |
| `inf` 与 `inf`、`-inf` 与 `-inf` | 接近（经 `a == b` 短路，与平台无关） |
| `inf` 与 `-inf`、无穷与有限值 | 不接近，任何容差都不例外 |
| `+0.0` 与 `-0.0` | 视为相等（遵循 IEEE 754 `==`） |
| 容差为负数、NaN、无穷 | 抛出 `ValueError`；非数值类型抛 `TypeError` |

实现只依赖 `math.isnan/isinf/isfinite` 与 IEEE 754 比较运算，
不经过任何平台相关路径，因此在 CPython 各平台行为一致。
大数异号相减溢出为 `inf` 时，判定稳定返回 False，不抛异常。

## 与其他实现的差异

- **`math.isclose`**：语义完全相同（本库即采用该规则），
  有限值上的结论由测试 `test_agrees_with_math_isclose_on_finite` 交叉验证。
  差异仅在于本库额外拒绝 NaN/无穷容差，并提供 `tolerance`/`rel_error` 辅助函数。
- **`numpy.isclose`**：判定式为 `|a-b| <= atol + rtol*|b|`，
  **不对称**（`b` 被视为“参考值”，交换参数结论可能相反），
  且两个容差是相加而非取较大者，默认 `atol=1e-08`。
  本库用 `max(|a|,|b|)` 保证对称，用 `max()` 组合保证单调性。
- **`unittest.assertAlmostEqual`**：按十进制位数 `round(a-b, places)` 判定，
  是“绝对舍入”语义，对大数过严、对小数过松，且无相对容差概念。
- **`pytest.approx`**：默认相对容差 `1e-6`，对零有特殊的绝对容差处理；
  其相对基准是被比较对象自身，与本库的对称口径不同。

## 边界用例（见 `TestBoundaryCases`）

- **极小值**：`5e-324`（最小非规格化数）与 0，纯相对容差下不接近，
  `abs_tol=5e-324` 时接近。
- **极大值**：`1.7976931348623157e308` 与其 `math.nextafter` 邻居，
  相对间隔约 1.1e-16；`rel_tol=1e-16` 不接近、`1e-15` 接近。
- **跨零点**：`-1e-12` 与 `1e-12`，相对容差无法弥合，`abs_tol` 可以。
- **十个数量级**：`1.0` 与 `1e±10` 默认容差下不接近；
  同量级内 `1e-10` 相对扰动接近；`1e16 + 1` 不可分辨、`1e16 + 1e8` 可分辨。

## 运行

```sh
python3 -m unittest test_numclose -v   # 运行全部性质断言与边界用例
python3 -c "from numclose import isclose; print(isclose(1.0, 1.0+1e-10))"
```
