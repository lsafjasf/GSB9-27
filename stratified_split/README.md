# stratified_split — 分层切分库（纯标准库 Python 3）

按分层键把样本切分为多个子集（train/val/...），每层比例贴近目标，
稀有类别有最小样本保底，结果完全可复现且与输入顺序无关。

## 运行

```bash
cd stratified_split
python3 demo.py                              # 分布对比报告
python3 -m unittest test_stratified_split -v # 14 个自测
```

## API

```python
from stratified_split import stratified_split, distribution_report, format_report

splits = stratified_split(
    items,
    key_fn=lambda x: x.label,          # 分层键
    ratios={"train": 0.8, "val": 0.2}, # 目标比例，和必须为 1，支持任意多个子集
    seed=2024,                         # 相同输入多重集 + 种子 => 完全相同的结果
    min_per_split=1,                   # 每层每侧最小样本数（受类别大小限制）
)
report = distribution_report(items, key_fn, ratios, splits)
print(format_report(report))           # 每层 count/actual/target/deviation 对比
```

## 设计要点

- **可复现**：类内按 `sha256(seed | repr(item))` 稳定排序，类别按 `repr` 排序遍历；
  不依赖 `dict` 插入顺序、`PYTHONHASHSEED` 或输入顺序，跨进程一致。
- **比例贴近**：每层先对整体做最大余数法分配，偏差不超过一个样本份额。
- **稀有类别策略**（n=类别样本数，k=子集数，m=min_per_split）：
  - `n >= k*m`：低于保底的子集从盈余最多的子集逐一补齐，保证每侧至少 m 个；
  - `n < k*m`：轮询分配，起点按类别由种子确定性地旋转，避免所有稀有类
    样本落入同一侧（纯随机切分的典型失败模式）。
- **重复样本**：按多重集语义处理，切分后两侧并集与输入完全一致。

## 测试覆盖

可复现性（同输入同输出 / 输入乱序无关 / 换种子不同 / 无重叠无丢失）、
比例偏差上界、稀有类保底、单类别、类别数大于样本数、每类单样本、
重复样本、空输入、非法比例。
