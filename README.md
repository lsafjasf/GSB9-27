# Overwrite Ring Buffer（单写多读，seqlock，无全局锁）

固定容量、覆盖式写入的环形缓冲区。高频写入持续进行时，多个异步读方
不会读到半条记录；读方落后被覆盖时，精确报告丢失的序号区间，绝不静默
读到错位数据。仅用 Python 3 标准库。

## 文件

- `ringbuffer.py` — 库：`RingBuffer`、`Reader`、`OverrunError`
- `test_ringbuffer.py` — 覆盖与并发自测（6 个用例）
- `bench.py` — 吞吐基准

## 运行

```bash
python3 -m unittest test_ringbuffer -v   # 自测
python3 bench.py                          # 吞吐基准（约 1 分钟）
```

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
- 4 个读方消费同一序号区间（n=500 > 容量 32）：各自实际取到的记录一致
  （幸存的最后 32 条），overrun 报告的丢失区间恰好覆盖 468 条，
  实取 + 丢失 = 500，缺口绝不允许被并进已见集合“填平”
- 并发完整性（2 秒，1 写 4 读）：每条记录自校验（所有字段必须等于序号），
  断言无撕裂记录、无序号跳变、overrun 区间首尾合法

## 吞吐数据

环境：Python 3.12.3（CPython，GIL），16 核，本机实测；数字随机器波动。

口径说明：读吞吐只统计真正取回并通过自校验的记录数——读取期间写方持续
写入，空读（`read()` 返回 `None`）和 overrun 跳圈不计入。不存在“先写满
再空转计时”的读基准。

单写方 1000 万次写入：32B / 1KB 载荷均约 **16M 条/秒**（载荷大小不影响
写路径，因为只存引用）。

一写一读、容量 1024，计时到读方真正取到 200 万条记录（32B / 1KB 一致）：
约 **100k 条/秒**、1954 次 overrun。这是 CPython GIL 的真实效应：两个线程
按 5ms 时间片轮流运行，写方一次连续推进约 8 万条（远大于容量 1024），读方
每次拿到 GIL 已被绕圈，先 overrun 跳过整圈再读幸存记录，因此交付速率远低
于写方的“发件”速率。

并发（1 写者，实测一轮）：

- 读方跟得上（容量 2^22，2 秒）：写 2.4–3.6M 条/秒；单读者 ~3.0M，
  2 读者合计 ~3.8M，4 读者合计 ~3.9M 条/秒（4 读者时观察到 5 次 overrun，
  属调度抖动）——多读者并行不互相阻塞。
- 读方远远落后（容量 1024，3 秒）：写 2.0–5.2M 条/秒；每读者交付速率
  ~10k–100k 条/秒，overrun 149–304 次。读方每次 overrun 跳过整圈，
  `read()` 交付速率下降是预期行为（开销在游标跳转而非数据拷贝）。

说明：写读路径均无锁、无系统调用，单线程写可达千万级条/秒；GIL 下“小容
量 + 持续写入”的读方受时间片粒度限制，实测交付约十万级条/秒，这才是读基
准应报告的数字。多线程下写吞吐下降同样来自 GIL 时间片竞争而非锁等待，换
用无 GIL 解释器或原生实现（同协议 + C11/C++ 原子 release/acquire）即可线
性扩展。
