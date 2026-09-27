# 死锁检测库说明

纯 Python 3 标准库实现，无任何第三方依赖。

## 原理

维护一张**等待图（wait-for graph）**：

- 节点：线程
- 有向边 `T1 -> T2`：线程 T1 正在等待 T2 持有的某把锁（边上记录锁名、等待开始时间、超时截止时间）
- 图中出现**有向环**即死锁

检测时枚举图中所有基本环，每个环输出：

- 完整环路径：`谁 --(等哪把锁, 等了多久)--> 持有者`
- 环上每个持有者的持锁时长
- 多个环分别报告，按环的阻塞时长（最老等待边的等待时长）降序排序

## 误判排除规则

| 场景 | 规则 | 实现 |
|---|---|---|
| 条件变量等待 | `Condition.wait()` 期间线程已释放底层锁，语义上是"等通知"而非"等锁"，不登记等待边；被唤醒后对底层锁的重新获取同样不登记 | 线程进入 wait 时标记 `cond_waiter` 并清除其持有者记录，期间一切边登记被抑制（`enter_cond_wait` / `exit_cond_wait`） |
| 可重入锁 | 同一线程重复 acquire 自己持有的 RLock 不会阻塞，不产生等待边，也绝不产生自环 | `TrackedRLock` 识别 `_owner == 当前线程` 直接放行；图中 waiter == holder 的边一律不登记 |
| 超时锁 | `acquire(timeout=...)` 的等待边标记 `expires_at`，**整条边不参与成环**——它迟早自行断开，含超时边的"环"必然不是死锁 | 环检测快照直接过滤 `has_timeout` 的边 |
| 非阻塞获取 | `acquire(blocking=False)` 不存在等待，不登记边 | 立即返回，失败也不留边 |

此外，`release` 时**先清除持有者记录再释放底层锁**：宁可短暂漏一条边，也不留假边，进一步压低误报率。

## 文件

- `deadlock_detector.py` — 库（`TrackedLock` / `TrackedRLock` / `TrackedCondition` / `detector`），`__main__` 内置两线程死锁演示
- `test_deadlock_detector.py` — 构造场景自测（7 个用例）

## 运行命令

```bash
cd deadlock

# 跑全部自测
python3 -m unittest test_deadlock_detector -v

# 看一次真实死锁的检测报告演示
python3 deadlock_detector.py
```

## 业务接入方式

```python
from deadlock_detector import TrackedLock, TrackedRLock, TrackedCondition, detector

lock = TrackedLock("order-lock")        # 替换 threading.Lock，接口一致
rlock = TrackedRLock("cache-lock")      # 替换 threading.RLock
cond = TrackedCondition(name="queue")   # 替换 threading.Condition

# 方式一：服务卡住时手动检测（可挂在 admin 接口 / 信号处理器里）
for report in detector.detect():
    print(report.format())

# 方式二：后台周期检测，发现死锁自动打印
detector.start_monitor(interval=1.0)
```

## 自测场景覆盖

| 用例 | 预期 |
|---|---|
| `test_two_thread_deadlock_detected` | 两线程交叉持锁 → 检出 1 个环，环路径与持锁时长正确 |
| `test_three_thread_deadlock_detected` | 三线程环形等待 → 检出完整三元环，路径方向正确 |
| `test_one_way_wait_is_not_deadlock` | 单向排队等锁 → 不报死锁 |
| `test_condition_wait_is_not_deadlock` | 条件变量等待 + 唤醒后重取锁被阻塞 → 均不误判 |
| `test_timeout_lock_breaks_cycle` | 环上一条边是超时等待 → 不报死锁，超时后各方正常推进 |
| `test_reentrant_lock_no_self_cycle` | 同线程三重 acquire RLock → 不产生自环 |
| `test_multiple_cycles_reported_and_sorted` | 两个独立死锁环 → 分别报告、互不串线、按阻塞时长降序 |

## 已知边界

- 检测的是**进程内**线程间死锁；跨进程/跨机器（如分布式锁）不在范围内
- 等待边在"即将阻塞"前登记，释放/获取的瞬时竞态可能造成毫秒级的漏边（不漏报优先于误报）
- 未包装的裸 `threading.Lock` 不参与检测，需替换为 `Tracked*` 版本
