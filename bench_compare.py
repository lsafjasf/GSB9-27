"""对拍与开销基准：采样聚合 vs 全量统计 vs 无探针真值。

输出三部分：
1. 占比对拍：人为构造 70%/30% 调用序列，给出无探针真值、全量、
   采样（多档频率）的累计占比与偏差。
2. 短调用密集场景：展示全量插桩对占比的扭曲与采样的稳健性。
3. 开销：各方案对被测程序的墙钟时间影响。
"""

import time

from stack_sampler import (
    FullTracer,
    StackSampler,
    aggregate_by_key,
    format_tree,
)


def spin(n):
    x = 0
    for i in range(n):
        x += i
    return x


def spin_for(seconds):
    end = time.perf_counter() + seconds
    x = 0
    while time.perf_counter() < end:
        x += 1
    return x


# ---- 场景一：长块 70/30（插桩开销可忽略，用于标定采样精度） ----

def leaf_a():
    spin_for(0.0007)


def mid_a():
    leaf_a()


def leaf_b():
    spin_for(0.0003)


def workload_long(duration):
    end = time.perf_counter() + duration
    while time.perf_counter() < end:
        mid_a()
        leaf_b()


# ---- 场景二：短调用密集（每次调用仅几微秒） ----

def tiny_a():
    spin(700)


def tiny_mid():
    tiny_a()


def tiny_b():
    spin(300)


def workload_tiny(duration):
    end = time.perf_counter() + duration
    while time.perf_counter() < end:
        tiny_mid()
        tiny_b()


def agg_get(agg, suffix):
    self_v, total_v = 0.0, 0.0
    for key, val in agg.items():
        if key == suffix or key.endswith("." + suffix):
            self_v += val[0]
            total_v += val[1]
    return self_v, total_v


def ground_truth(a_fn, b_fn, duration=0.6):
    """无探针真值：交错执行 A/B 并分别计时。

    必须交错测量——分段先后测量会受 CPU 频率漂移影响，
    实测同一台机器上两次分段结果可相差 2pp 以上。
    perf_counter 开销（~50ns）相对微秒级调用可忽略。
    """
    ta = tb = 0.0
    end = time.perf_counter() + duration
    while time.perf_counter() < end:
        t0 = time.perf_counter()
        a_fn()
        t1 = time.perf_counter()
        b_fn()
        t2 = time.perf_counter()
        ta += t1 - t0
        tb += t2 - t1
    return ta / (ta + tb)


def run_full(workload, duration):
    tracer = FullTracer()
    tracer.start()
    workload(duration)
    tracer.stop()
    return tracer


def run_sampled(workload, duration, interval, jitter=0.5):
    with StackSampler(interval=interval, jitter=jitter) as sampler:
        workload(duration)
    return sampler


def compare_scenario(title, workload, a_key, b_key, a_fn, b_fn, duration=1.5):
    print(f"\n=== {title} ===")
    truth = ground_truth(a_fn, b_fn)
    print(f"无探针真值（分段计时）: {a_key} cum = {truth*100:.2f}%")

    tracer = run_full(workload, duration)
    full = aggregate_by_key(tracer.root, "time")
    full_total = tracer.root.total_time
    fa = agg_get(full, a_key)[1] / full_total
    fb = agg_get(full, b_key)[1] / full_total
    print(f"全量统计            : {a_key} cum = {fa*100:.2f}%  "
          f"{b_key} cum = {fb*100:.2f}%  (对真值偏差 {abs(fa-truth)*100:.2f}pp)")

    print(f"{'采样间隔':>10} {'样本数':>7} {'丢失率':>7} "
          f"{a_key+' cum':>12} {'对真值偏差':>10} {'对全量偏差':>10}")
    for interval in (0.01, 0.001, 0.0002):
        sampler = run_sampled(workload, duration, interval)
        agg = aggregate_by_key(sampler.root, "count")
        total = sampler.root.total_count
        sa = agg_get(agg, a_key)[1] / total
        rep = sampler.loss_report()
        print(f"{interval*1000:>8.1f}ms {rep['taken']:>7} "
              f"{rep['loss_rate']*100:>6.2f}% {sa*100:>11.2f}% "
              f"{abs(sa-truth)*100:>9.2f}pp {abs(sa-fa)*100:>9.2f}pp")
    return tracer


def bench_overhead():
    print("\n=== 采样/全量开销（墙钟时间，3 轮交错取最小，1.00x=无开销） ===")

    def _timed_once(fn):
        t0 = time.perf_counter()
        fn()
        return time.perf_counter() - t0

    # 校准使 baseline 约 0.3s
    spin(100000)  # 预热
    t0 = time.perf_counter()
    spin(100000)
    unit = time.perf_counter() - t0
    n = int(0.3 / unit * 100000)

    def cpu_work():
        spin(n)

    def call_work():
        for _ in range(n // 10):
            tiny_b()

    def sampled(work, interval):
        sampler = StackSampler(interval=interval, jitter=0.2).start()
        work()
        sampler.stop()

    def traced(work):
        tracer = FullTracer().start()
        work()
        tracer.stop()

    # 所有方案放进列表，3 轮交错测量取最小值，
    # 避免 CPU 频率漂移让先测的方案吃亏
    configs = [("无探针 baseline", cpu_work, call_work)]
    for interval in (0.01, 0.001, 0.0002):
        configs.append((
            f"采样 interval={interval*1000:.1f}ms",
            lambda i=interval: sampled(cpu_work, i),
            lambda i=interval: sampled(call_work, i),
        ))
    configs.append((
        "全量 sys.setprofile",
        lambda: traced(cpu_work),
        lambda: traced(call_work),
    ))

    best = {label: [None, None] for label, _, _ in configs}
    for _ in range(3):
        for label, cpu_fn, call_fn in configs:
            for idx, fn in enumerate((cpu_fn, call_fn)):
                dt = _timed_once(fn)
                if best[label][idx] is None or dt < best[label][idx]:
                    best[label][idx] = dt

    base_cpu, base_call = best["无探针 baseline"]
    print(f"{'方案':<28}{'计算密集':>14}{'调用密集':>14}")
    for label, _, _ in configs:
        t_cpu, t_call = best[label]
        if label == "无探针 baseline":
            print(f"{label:<28}{t_cpu:>12.3f}s{t_call:>12.3f}s")
        else:
            print(f"{label:<28}{t_cpu/base_cpu:>13.2f}x{t_call/base_call:>13.2f}x")


def main():
    print("=" * 64)
    print("调用栈采样 vs 全量统计 对拍报告")
    print("=" * 64)

    tracer = compare_scenario(
        "场景一：长块 70/30（A=0.7ms B=0.3ms 交替）",
        workload_long, "mid_a", "leaf_b", mid_a, leaf_b,
    )

    compare_scenario(
        "场景二：短调用密集 70/30（每次调用仅数微秒）",
        workload_tiny, "tiny_mid", "tiny_b", tiny_mid, tiny_b,
    )

    print("\n=== 采样聚合树示例（场景一，interval=1ms） ===")
    sampler = run_sampled(workload_long, 1.0, 0.001)
    print(format_tree(sampler.root, sampler.root.total_count, min_pct=0.5))
    print("loss:", sampler.loss_report())

    print("\n=== 全量聚合树示例（场景一） ===")
    print(format_tree(tracer.root, tracer.root.total_time, mode="time",
                      min_pct=0.5))

    bench_overhead()


if __name__ == "__main__":
    main()
