# 日志抑制器（logsup）

故障期间同类日志每秒数万条刷爆磁盘时，按**模板归并**抑制重复日志：
首次出现全量保留，窗口内重复只计数，窗口到期输出
`[SUPPRESSED] 过去 N 秒重复 M 次 | template=... | 参数分布: ...`。

## 设计要点

- **模板归并**：UUID / IP:port / hex / 引号串 / 数字统一归一化为 `*`，
  形状相同的日志归为一组，参数单独提取。
- **新模板立即放行**：第一次见到某模板时原样输出，绝不进抑制窗口。
- **汇总保留信息**：每条汇总带被抑制样本的参数分布（top 值 + distinct 计数）。
- **内存硬上界**：模板状态数不超过 `max_templates`，超出按 **LRU 淘汰**；
  被淘汰模板若有未发出汇总会先冲刷，不丢信息。每模板样本数、
  每参数 distinct 集合也都有界。
- **退出前 `flush()`**：冲刷所有未发出汇总。

## 运行

```bash
cd logsup
python3 -m unittest -v        # 11 个单元测试（含内存上界、新模板放行）
python3 burst_benchmark.py    # 突发场景量化（抑制率 / 信息保留 / 内存峰值）
```

## 用法

```python
from log_suppressor import LogSuppressor

sup = LogSuppressor(interval=10.0, max_templates=10_000)
for line in sup.process("ERROR db connect 10.0.0.7:5432 timeout 30s"):
    write_to_disk(line)
# 进程退出前
for line in sup.flush():
    write_to_disk(line)
```

## 实测数据（burst_benchmark.py）

| 场景 | 输入 | 输出 | 抑制率 | 模板状态峰值 |
|---|---|---|---|---|
| 单条高频 3 万条/秒 x 10s | 18.66 MB | 0.003 MB（10 首条 + 10 汇总） | 99.984% | 1 / 10000 |
| 海量不同模板 5 千新模板/秒 | 50,000 条全部放行 | 0% 抑制（正确：全是新模式） | 0% | 1000 / 1000（LRU 淘汰 49,000 次） |
| 混合突发 2 万条/秒 | 7.35 MB | 0.012 MB（70 首条 + 70 汇总） | 99.837% | 16 / 10000 |
