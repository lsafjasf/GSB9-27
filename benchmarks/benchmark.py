"""清理开销与速率基准：只使用标准库。

场景 1：写入吞吐 + 海量对象同时过期时的分批清理开销（每批工作量上界）。
场景 2：稳态下“清理速率 vs 写入速率”——给出不同 budget/批次频率时，
        清理能力（对象/秒）相对写入速率的倍数。

运行：python3 benchmarks/benchmark.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lifecycle import FakeClock, LifecycleStore


def measure(label, fn, repeat=1):
    best = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        elapsed = time.perf_counter() - start
        best = elapsed if best is None else min(best, elapsed)
    print(f"{label:<58} {best*1000:9.2f} ms")
    return best, result


def scenario_mass_expiry(n=200_000, budget=4096):
    print(f"== 场景 1：{n:,} 个对象全部同时过期（budget={budget}/批） ==")
    clock = FakeClock(0)
    store = LifecycleStore(clock=clock)

    t_put, _ = measure(f"写入 {n:,} 个对象 (put)", lambda: [
        store.put(i, i, ttl=100, size=1) for i in range(n)
    ])
    print(f"    写入速率：{n/t_put:,.0f} 对象/秒")

    clock.advance(101)
    t0 = time.perf_counter()
    batches = 0
    inspected = 0
    reclaimed = 0
    max_inspected = 0
    while True:
        r = store.sweep_batch(budget)
        batches += 1
        inspected += r.inspected
        reclaimed += r.reclaimed
        max_inspected = max(max_inspected, r.inspected)
        if r.early_stop or r.inspected < budget:
            break
    elapsed = time.perf_counter() - t0
    print(f"    物理清理（惰性过期后）：{elapsed*1000:.2f} ms，"
          f"批次数 {batches}")
    print(f"    清理速率：{reclaimed/elapsed:,.0f} 对象/秒；"
          f"清理/写入速率比：{(reclaimed/elapsed)/(n/t_put):.2f}x")
    print(f"    单批最大实际工作量 inspected={max_inspected} "
          f"(上界 {budget})；总 inspected={inspected:,}")
    print(f"    清理后 live_objects={store.stats().live_objects}，"
          f"heap_items={store.stats().heap_items}")
    print()
    return n / t_put, reclaimed / elapsed


def scenario_steady_state(write_rate_target=100_000, ttl_units=100):
    print("== 场景 2：稳态清理能力 vs 写入速率（单线程测量） ==")
    budgets = [256, 1024, 4096, 16384]
    results = []
    for budget in budgets:
        clock = FakeClock(0)
        store = LifecycleStore(clock=clock)
        # 预先放一批“已过期”对象作为清理负载
        warm = 50_000
        for i in range(warm):
            store.put(i, i, ttl=1, size=1)
        clock.advance(10)

        # 连续多批，测纯清理吞吐
        batches = 0
        reclaimed = 0
        start = time.perf_counter()
        while store.stats().live_objects > 0:
            store.sweep_batch(budget)
            batches += 1
        elapsed = time.perf_counter() - start
        sweep_rate = reclaimed_total = warm / elapsed
        results.append((budget, batches, sweep_rate))
        print(f"    budget={budget:>6}: {sweep_rate:>12,.0f} 对象/秒，"
              f"批次数 {batches:>4}，单批均摊 {elapsed/batches*1e6:8.1f} us/批")

    print()
    print("    解释：清理与写入共享同一把锁。稳态条件为")
    print("      清理能力 >= 过期产生速率 ≈ 写入速率（TTL 内对象都会到期）")
    print("    若后台清理线程每 interval 秒跑一批 budget 个对象，则")
    print("      清理速率上限 ≈ budget / interval (对象/秒)")
    print("    该值应 >= 峰值写入速率；上表给出了不同 budget 下单线程可持续的")
    print("    清理能力，可据此选 budget 与批次频率。")
    print()
    return results


def scenario_repeated_renewal():
    print("== 场景 3：10 万对象各反复续期 10 次后的墓碑清理 ==")
    clock = FakeClock(0)
    store = LifecycleStore(clock=clock)
    n = 100_000
    renews = 10

    def workload():
        for i in range(n):
            store.put(i, i, ttl=1000, size=1)
        for r in range(renews):
            clock.set(float(r))
            for i in range(n):
                store.renew(i, ttl=1000)
        clock.set(10_000)  # 全部最终过期

    t, _ = measure(f"{n:,} 对象 x (1 put + {renews} renew)", workload)
    stats = store.stats()
    print(f"    堆项数（含墓碑）：{stats.heap_items:,}；"
          f"活对象 {stats.live_objects:,}（物理）/"
          f"{stats.visible_objects:,}（可见）")

    reclaimed = 0
    stale = 0
    inspected = 0
    start = time.perf_counter()
    results = store.sweep_drain(budget_per_batch=8192)
    elapsed = time.perf_counter() - start
    for r in results:
        reclaimed += r.reclaimed
        stale += r.stale_heap_items
        inspected += r.inspected
    print(f"    清理 {elapsed*1000:.2f} ms：回收 {reclaimed:,} 对象，"
          f"丢弃墓碑 {stale:,}，inspected={inspected:,}，"
          f"批次数 {len(results)}")
    print(f"    墓碑处理速率：{(reclaimed+stale)/elapsed:,.0f} 堆项/秒"
          f"（含 {stale/(reclaimed+stale)*100:.0f}% 续期墓碑）")
    print()


if __name__ == "__main__":
    put_rate, sweep_rate = scenario_mass_expiry()
    scenario_steady_state()
    scenario_repeated_renewal()
    print("结论（本机）：")
    print(f"  纯清理吞吐约为写入吞吐的 {sweep_rate/put_rate:.1f} 倍，"
          "因此预算得当（如每秒清理预算 >= 峰值写入速率）时，")
    print("  分批惰性清理可持续回收全部过期对象，且单批延迟由 budget 严格上界。")
