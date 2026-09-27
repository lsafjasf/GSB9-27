# 趋势递增唯一 ID 生成器

## 结构
64 bit：`1 bit 符号位(0) | 41 bit 毫秒时间戳(相对 EPOCH=2024-01-01) | 10 bit 机器号 | 12 bit 序列号`

## 确定行为
- **序列耗尽**：同一毫秒 4096 个序列用完后，自旋等待下一毫秒（`time.sleep(0)` 让出 GIL），继续生成。
- **小幅回拨**（≤ `max_backward_ms`，默认 5ms）：等待时钟追平上次时间戳后再生成。
- **大幅回拨**（> `max_backward_ms`）：抛出 `ClockMovedBackwardsError`，异常携带 `offset_ms` 偏差值；拒绝期间不产出任何 ID，恢复后序列仍严格递增、无重复。
- **机器复制**：snowflake 类方案的固有约束——副本必须分配不同 `machine_id`（0..1023），非法值在构造时即 `ValueError`。

## 运行
```bash
python3 test_snowflake.py -v   # 11 个用例，含 8 线程 10 万 ID 唯一性断言
```

## 使用
```python
from snowflake import SnowflakeGenerator
gen = SnowflakeGenerator(machine_id=1)          # clock 可注入，便于测试
id_value = gen.next_id()
SnowflakeGenerator.parse(id_value)              # 反解时间/机器/序列
```
