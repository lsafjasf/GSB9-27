# collector — 采集端离线模式与降级

纯 Python 3 标准库实现，时间可注入（`Clock` 协议），不依赖任何第三方包。

## 设计

- **落盘缓冲**（`buffer.py`）：断网时记录追加写入 `buffer.jsonl`；
  `state.json` 持久化确认游标 `start` 与累计丢弃数 `dropped`，崩溃重启不丢状态。
- **上界与丢弃策略**：`max_records`（条数）与可选 `max_bytes`（字节）双上界；
  达到上界按 **DROP_OLDEST**（丢弃最旧未上报记录，保留最新数据）降级，
  丢弃计数持久化累计，通过 `DiskBuffer.dropped` / `Collector.dropped` 读取。
- **限速重发**（`collector.py` 的 `drain`）：令牌桶限速，速率 `resend_rate` 条/秒，
  单批上限 `batch_size`，桶容量 = `batch_size`（只允许一次初始突发），
  恢复后不会一股脑打满服务。`ResendMetrics` 输出速率与积压下降采样。
- **不重复上报**：每条记录带单调递增 id；游标只在 transport 明确返回
  “已接受 n 条”后推进（部分接受只确认部分，异常一条都不确认）；
  缓冲非空时新数据排队不插队。已确认数据绝不重发，未确认数据绝不丢失。
- **时间注入**：`Clock` 协议（`now`/`sleep`），生产用 `SystemClock`，
  测试/演示用 `FakeClock`（`clock.py`），零真实等待验证限速逻辑。

## 运行方式

```bash
# 回归测试（11 个用例）
python3 -m unittest tests.test_offline_mode -v

# 场景演示：输出丢弃计数、重发速率、积压下降曲线
python3 demo_offline.py
```

## 用法示例

```python
from collector import DiskBuffer, Collector, SystemClock

buf = DiskBuffer("/var/lib/mycollector", max_records=10_000, max_bytes=64 << 20)
coll = Collector(buf, transport=my_transport, clock=SystemClock(),
                 resend_rate=200.0, batch_size=50)

coll.emit({"cpu": 0.42})     # 在线直发；失败自动落盘
metrics = coll.drain()       # 恢复后限速重发，返回速率与积压采样
print(coll.dropped)          # 累计丢弃计数
```

`transport` 需实现 `send(batch) -> int`（返回服务端实际接受条数，断网抛异常）。

## 测试覆盖

| 用例 | 场景 |
| --- | --- |
| test_01 | 正常在线：实时直发、缓冲为空、零重复 |
| test_02 | 短暂断网：全部补发、零丢弃、零重复 |
| test_03 | 长时间断网：DROP_OLDEST 丢弃计数、保留最新数据、账目守恒 |
| test_04 | 大积压恢复：单批上界、令牌桶限速、积压单调下降、速率达标 |
| test_05 | 进程重启：游标/丢弃计数持久化，不重发已确认数据 |
| test_06 | 服务端部分接受：只确认已接受部分，零重复零丢失 |
| test_07 | 边界：上界为 1 |
| test_08 | 边界：字节数上界 |
| test_09 | 状态文件为合法 JSON |
| test_10 | 非法构造参数校验 |
| test_11 | 在线/断网/恢复/再断网全周期零重复 |
