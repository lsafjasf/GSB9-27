"""对拍：同一构造负载分别跑「全量统计」与「采样聚合」，输出占比偏差。

用法: python3 compare.py [采样间隔ms，默认1.0] [目标CPU秒数，默认2.5]
"""

from __future__ import annotations

import sys
import time

import workload
from fullprof import FullProfiler
from sampler import StackSampler


def calibrate(unit: int, target_cpu_s: float) -> int:
    """测一次 run_sequence 的 CPU 耗时，推算达到目标耗时需要的 repeats。"""
    t0 = time.process_time()
    workload.run_sequence(unit, 1)
    dt = time.process_time() - t0
    return max(1, round(target_cpu_s / max(dt, 1e-9)))


def run_full(unit: int, repeats: int) -> FullProfiler:
    prof = FullProfiler()
    originals = {}
    for name in workload.TRACED_FUNCS:
        originals[name] = getattr(workload, name)
        setattr(workload, name, prof.profile(name)(originals[name]))
    try:
        workload.run_sequence(unit, repeats)
    finally:
        for name, fn in originals.items():
            setattr(workload, name, fn)
    return prof


def run_sampled(unit: int, repeats: int, interval: float) -> StackSampler:
    sampler = StackSampler(interval=interval)
    sampler.start()
    workload.run_sequence(unit, repeats)
    sampler.stop()
    return sampler


def main() -> None:
    interval_ms = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    target_s = float(sys.argv[2]) if len(sys.argv) > 2 else 2.5
    unit = 50_000
    repeats = calibrate(unit, target_s)
    print(f"负载: unit={unit}, repeats={repeats} (目标 CPU {target_s}s), "
          f"采样间隔 {interval_ms}ms")

    prof = run_full(unit, repeats)
    full = prof.percentages()

    sampler = run_sampled(unit, repeats, interval_ms / 1000.0)
    tree = sampler.tree
    stats = tree.func_stats()
    n = tree.samples or 1

    print(f"\n采样: {n} 次, 丢失统计: {sampler.loss_report()}")
    print(f"\n{'function':<10} {'full_total%':>11} {'samp_total%':>11} {'Δtotal':>8}"
          f" {'full_self%':>10} {'samp_self%':>10} {'Δself':>8}")
    max_dtotal = max_dself = 0.0
    sum_dtotal = sum_dself = 0.0
    count = 0
    for name in workload.TRACED_FUNCS:
        f = full.get(name, {"self_pct": 0.0, "total_pct": 0.0})
        self_c, total_c = stats.get(name, (0, 0))
        s_total = 100.0 * total_c / n
        s_self = 100.0 * self_c / n
        dt = abs(f["total_pct"] - s_total)
        ds = abs(f["self_pct"] - s_self)
        max_dtotal, max_dself = max(max_dtotal, dt), max(max_dself, ds)
        sum_dtotal += dt
        sum_dself += ds
        count += 1
        print(f"{name:<10} {f['total_pct']:11.2f} {s_total:11.2f} {dt:8.2f}"
              f" {f['self_pct']:10.2f} {s_self:10.2f} {ds:8.2f}")
    print(f"\n平均偏差: total {sum_dtotal/count:.3f}pp, self {sum_dself/count:.3f}pp")
    print(f"最大偏差: total {max_dtotal:.3f}pp, self {max_dself:.3f}pp")

    print("\n采样聚合树:")
    for line in tree.lines():
        print(line)


if __name__ == "__main__":
    main()
