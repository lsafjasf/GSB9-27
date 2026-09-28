# 分块纠删码（Erasure Coding）

纯 Python 3 标准库实现的分块纠删码，用于在不使用多副本的前提下容忍多块同时损坏。

## 原理

- 在有限域 GF(2⁸)（256 个元素）上做 Reed–Solomon 编码。
- 数据均分为 `k` 个数据块，线性组合生成 `m` 个校验块：

  ```
  [D]   ( I(k)     )
  [ ] = (           ) * D
  [P]   ( Cauchy(m,k))
  ```

  编码矩阵上半部是单位阵（数据块原样保留），下半部是 Cauchy 矩阵。
  Cauchy 矩阵的任意方子矩阵都可逆，因此 **`k+m` 个块中任意 `m` 个
  （无论数据块还是校验块）同时损坏/丢失都能逐字节恢复**；存活块不足
  `k` 个（损坏数 `> m`）时抛出 `UnrecoverableError`。
- 每块附 SHA-256 校验和，损坏块（内容被篡改/翻转）会被识别并丢弃，
  与整块丢失（`None`）走同一条恢复路径。
- 限制：`1 ≤ k ≤ 255`，`m ≥ 1`，`k + m ≤ 256`（GF(2⁸) 单域最多 256 块）。
- 字节运算用 `bytes.translate` 的 GF 乘法查表 + 大整数 XOR 加速。

## 用法

```python
from erasure.erasure_code import encode, decode, UnrecoverableError

enc = encode(data, k=8, m=4)        # 8 数据块 + 4 校验块，可容忍任意 4 块损坏
blocks = list(enc.blocks)
blocks[0] = b"..."                  # 数据块被篡改
blocks[11] = None                   # 校验块整块丢失
recovered = decode(enc, blocks)     # b"...原始数据..."
assert recovered == data
```

恢复能力：`enc.tolerable_losses() == m`。

## 自测与运行命令

```bash
python3 erasure/selftest.py         # 或 python3 -m erasure.selftest
```

自测内容：
1. 边界：零长度数据、单字节数据、最小配置 `k=1,m=1`、非法参数；
2. 恢复组合：对损坏块数 `0..m` 穷举全部 C(n,t) 组合（组合过多时随机抽样），
   并分别统计「仅数据块 / 仅校验块 / 混合」损坏，恢复结果与原始数据逐字节比对；
3. 损坏 `m+1` 块（超上限）抽查必须抛 `UnrecoverableError`；
4. 输出冗余开销与编码/恢复耗时。

## 块数、开销、恢复代价的关系

- 冗余开销 ≈ `m/k`（校验字节总量 / 原始字节）。与 3 副本的 200% 相比，
  如 `k=10,m=4` 仅 40%、`k=12,m=2` 仅约 17%。
- `m` 越大可容忍损坏块数越多，但开销线性上升；`k` 越大开销越低，
  但单块更小、块数更多，编解码计算量增大。
- 编码计算量约 `O(m·k)` 次块向量运算；恢复需对幸存块做 GF 上 Gauss–Jordan
  消元，约 `O(k²)` 次块向量运算，代价随 `k`（不是损坏块数）平方增长；
  只损坏校验块时代价最低（数据块完整可直接拼接）。
