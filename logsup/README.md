# logsup — 日志抑制器（纯标准库 Python 3）

故障期间同类日志每秒数万条刷爆磁盘时，按模板归并：
首次出现全量保留，后续按间隔输出「过去 N 秒重复 M 次」汇总，
并附被抑制样本的参数分布摘要（如 `p0=[sda1×200001]`）。

## 设计要点

- **模板归并**：UUID/IP/hex/引号串/含数字标识符/带单位数字 → `{}`，同模板归为一类。
- **新模板立即放行**：首条不受任何抑制窗口影响，故障中新错误模式绝不丢失。
- **状态硬上界**：`max_templates` 超出时 LRU 淘汰最久未活动模板，
  淘汰前冲刷 `SUPPRESSED-FINAL` 最终汇总，不丢计数；参数分布每位置最多
  8 个真实取值 + `<other>` 桶，抑制器自身内存与流量解耦。
- **收尾**：`flush()` 把仍挂着未汇报计数的模板各冲一条最终汇总。

## 运行

```bash
cd logsup
python3 -m unittest test_log_suppressor -v   # 12 个自测
python3 burst_demo.py                        # 突发场景量化
```

## 用法

```python
from log_suppressor import LogSuppressor

sup = LogSuppressor(interval=10.0, max_templates=10_000)
for line in log_stream:
    for out in sup.process(line):   # 返回空列表 = 被抑制
        sink.write(out)
for out in sup.flush():             # 进程退出前收尾
    sink.write(out)
print(sup.stats())                  # 抑制率 / 字节缩减 / 淘汰数
```
