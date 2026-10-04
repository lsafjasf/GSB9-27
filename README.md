# Overwrite Ring Buffer（单写多读，seqlock，无全局锁）

固定容量、覆盖式写入的环形缓冲区。高频写入持续进行时，多个异步读方
不会读到半条记录；读方落后被覆盖时，精确报告丢失的序号区间，绝不静默
读到错位数据。仅用 Python 3 标准库。

## 文件

- `ringbuffer.py` — 库：`RingBuffer`、`Reader`、`OverrunError`
- `test_ringbuffer.py` — 覆盖与并发自测（6 个用例）
- `bench.py` — 吞吐基准
- `prioritize.py` — 用例排序库：按覆盖率增量贪心排序（见下节）
- `test_prioritize.py` — 排序库自测（12 个用例，含边界情形）
- `prioritization_data.json` — 示例排序结果与累积曲线数据

## 运行

```bash
python3 -m unittest test_ringbuffer -v   # 自测
python3 bench.py                          # 吞吐基准（约 20s）
python3 -m unittest test_prioritize -v    # 用例排序库自测
python3 prioritize.py                     # 打印示例排序结果 + 累积曲线（JSON）
```

## 用例排序（按覆盖率增量）

`prioritize.py` 解决回归用例太多跑不完的问题：按覆盖率增益贪心排序，
优先执行能覆盖新代码的用例。

- `prioritize(coverage)` — 输入 `{用例id: {覆盖行集合}}`，返回排序后的
  用例 id 列表。每步选边际增益最大的用例；增益相同时按确定性规则
  打破平局：总覆盖行数多者优先，再按用例 id 字典序。规则是候选集上
  的全序，因此排序结果只取决于覆盖率集合本身，与输入顺序无关
  （自测中对全部排列做了断言）。
- `cumulative_curve(coverage, order=None, top=None)` — 沿排序结果给出
  累积曲线：`rank / test / gain / covered / pct`，`top` 可只取前若干行。
- 边界行为：完全重叠的用例首个之后增益为 0；互不重叠时按集合大小
  排序；未覆盖任何行的用例沉到队尾（按同一确定性规则排列）；空集合
  输入返回空列表；全部无覆盖时 pct 记为 100。

示例数据见 `prioritization_data.json`（8 个用例，前 5 个即达 100% 覆盖：
42.86% → 64.29% → 85.71% → 92.86% → 100%）。

## 使用

```python
from ringbuffer import RingBuffer, OverrunError

buf = RingBuffer(1024)
r = buf.reader()                 # 每个读方独立游标
buf.write(record)
try:
    seq, record = r.read()       # 无数据返回 None
except OverrunError as e:
    e.lost_from, e.lost_to       # 丢失序号闭区间；游标已自动跳到现存最旧记录
```

写序号从 1 开始单调递增，永不复用；`next_seq` 由每个 `Reader` 独立维护。
`write()` 只能由单写线程调用（单写者模型，多写者需在外部串行化）。

## 并发与内存屏障设计

**不使用全局锁**，读写路径上没有任何 `Lock`。采用“每槽序号（seqlock）+
全局写序号”：

写方（有序执行）：
1. `seq = _write_seq + 1`
2. 数据写入槽 `_slots[idx]`
3. `_slot_seq[idx] = seq` —— release store，发布该槽数据
4. `_write_seq = seq` —— release store，推进写头

读方：
1. 读取 `_write_seq`（acquire）
2. overrun 检查（见下）
3. 读 `s1 = _slot_seq[idx]`（acquire）→ 读数据 → 再读 `s2 = _slot_seq[idx]`
4. 仅当 `s1 == s2 == 期望序号` 才接受；否则说明该槽在读的过程中被覆盖，
   回到第 1 步重新判定/重试 —— 永远不会返回撕裂数据

**overrun 判定**：若 `_write_seq - next_seq >= capacity`，则
`[next_seq, _write_seq - capacity]` 已永久丢失，抛 `OverrunError` 报告该
闭区间，并把游标移到现存最旧记录。要么读到序号严格等于 `next_seq` 的
记录（经 seqlock 校验），要么收到精确的丢失区间，不存在第三种情况。

**屏障**：CPython 的 GIL 使每条字节码原子执行且存储总序一致（TSO），上面
有序的 store 即 release、load 即 acquire，读方的二次序号读取封住了“读数据
期间写方整圈覆盖”的竞态。算法本身与架构无关；在无 GIL 解释器上只需把序号
的存取换成 release/acquire 原子操作（如 `atomic`），协议不变。

## 自测覆盖

- 容量为 1：写 5 条后正确报告丢失 `[1,4]`，只剩最新一条
- 写入量远大于容量（容量 8，写 80000）：丢失区间到 `total-cap`，最后
  `cap` 条完整有序
- 读方长时间停滞后追赶：停滞期间被绕圈多次，overrun 区间正确，之后
  逐条读完所有幸存记录
- 4 个读方消费同一序号区间：彼此独立、内容一致、序号 1..N 无缺
- 并发完整性（2 秒，1 写 4 读）：每条记录自校验（所有字段必须等于序号），
  断言无撕裂记录、无序号跳变、overrun 区间首尾合法

## 吞吐数据

环境：Python 3.12（CPython，GIL），本机实测；数字随机器波动。

单线程无竞争（1000 万次操作）：

| 载荷 | 写 | 读 |
|---|---|---|
| 32B | ~13.5–16M 条/秒 | ~30M 条/秒 |
| 1KB | ~13.5–16M 条/秒 | ~30M 条/秒 |

并发（1 写者）：

- 读方跟得上（容量 2^22，0 overrun）：写 ~3–4M 条/秒；单读者 ~3.3M，
  2 读者合计 ~4.1M，4 读者合计 ~4.0M 条/秒 —— 多读者并行不互相阻塞。
- 读方远远落后（容量 1024）：写 ~1.7–5.6M 条/秒；读方每次 overrun 跳过
  整圈，`read()` 返回速率下降是预期行为（开销在游标跳转而非数据拷贝）。

说明：写读路径均无锁、无系统调用，单线程可达千万级条/秒；多线程下写吞吐
下降主要来自 CPython GIL 的时间片竞争，而非锁等待，换用无 GIL 解释器或
原生实现（同协议 + C11/C++ 原子 release/acquire）即可线性扩展。
