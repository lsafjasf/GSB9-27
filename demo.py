"""快速演示：过期可见性、分批清理指标、续期保护。

运行：python3 demo.py
"""

from lifecycle import FakeClock, LifecycleStore

clock = FakeClock(0)
store = LifecycleStore(clock=clock)

# 1) 写入：两个 100 字节对象在 t=10 过期，一个在 t=100
store.put("tmp-1", "A" * 90, ttl=10, size=100)
store.put("tmp-2", "B" * 90, ttl=10, size=100)
store.put("keep",  "C" * 90, ttl=100, size=100)

print("t=0  可见对象:", store.stats().visible_objects)
clock.set(10)
print("t=10 立即不可见？", "tmp-1" not in store, "；物理条目还在？",
      store.stats().live_objects == 3)

# 2) 清理批次：budget=1 -> 先回收 1 个；被 pin 的情况见测试
r1 = store.sweep_batch(budget=1)
print(f"第1批 budget=1: inspected={r1.inspected} 回收={r1.reclaimed} "
      f"回收字节={r1.reclaimed_bytes}")
r2 = store.sweep_batch(budget=10)
print(f"第2批 budget=10: inspected={r2.inspected} 回收={r2.reclaimed} "
      f"跳过(未过期)={r2.skipped_not_expired} early_stop={r2.early_stop}")

# 3) 续期保护：读取窗口内过期，清理跳过
clock.set(0)
store.put("pinned", "D" * 90, ttl=10, size=100)
with store.begin_access("pinned"):
    clock.set(10)
    r3 = store.sweep_batch(10)
    print(f"读取中过期批次: 跳过(pinned)={r3.skipped_pinned} 回收={r3.reclaimed} "
          f"blocked={r3.blocked_pinned}")
r4 = store.sweep_batch(10)
print(f"读取结束后批次: 回收={r4.reclaimed}")

s = store.stats()
print(f"累计: 批次={s.total_batches} inspected={s.total_inspected} "
      f"回收对象={s.total_reclaimed} 回收字节={s.total_reclaimed_bytes} "
      f"跳过pinned={s.total_skipped_pinned} 墓碑={s.total_stale_heap_items}")
