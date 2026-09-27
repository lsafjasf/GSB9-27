"""采样开销测量：同一负载在「无采样 / 1kHz / 5kHz」下的 CPU 耗时。

用法: python3 overhead.py [目标CPU秒数，默认2.0]
"""

from __future__ import annotations

import sys
import time

import workload
from sampler import StackSampler


def calibrate(unit: int, target_cpu_s: float) -> int:
    t0 = time.process_time()
    workload.run_sequence(unit, 1)
    return max(1, round(target_cpu_s / max(time.process_time() - t0, 1e-9)))


def measure(unit: int, repeats: int, interval_ms: float | None) -> dict:
    # 每个配置跑 3 次取最小（最接近真实开销）
    times = []
    for _ in range(3):
        sampler = StackSampler(interval_ms / 1000.0) if interval_ms else None
        t0 = time.process_time()
        if sampler:
            sampler.start()
        workload.run_sequence(unit, repeats)
        if sampler:
            sampler.stop()
        times.append(time.process_time() - t0)
        last = sampler
    return {
        "cpu_s": min(times),
        "loss": last.loss_report() if interval_ms else None,
    }


def main() -> None:
    target_s = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
    unit = 50_000
    repeats = calibrate(unit, target_s)
    print(f"负载: unit={unit}, repeats={repeats} (目标 CPU {target_s}s)，每配置取 3 次最小值\n")

    baseline = measure(unit, repeats, None)["cpu_s"]
    print(f"{'配置':<14} {'CPU耗时s':>10} {'相对开销':>10} {'样本数':>8} {'单次采样us':>12}")
    print(f"{'无采样':<14} {baseline:10.4f} {'—':>10} {'—':>8} {'—':>12}")
    for label, interval_ms in (("1kHz (1ms)", 1.0), ("5kHz (0.2ms)", 0.2),
                               ("10kHz (0.1ms)", 0.1)):
        r = measure(unit, repeats, interval_ms)
        overhead_pct = 100.0 * (r["cpu_s"] - baseline) / baseline
        loss = r["loss"]
        per_sample_us = (
            1e6 * (r["cpu_s"] - baseline) / max(loss["taken"], 1)
        )
        print(f"{label:<14} {r['cpu_s']:10.4f} {overhead_pct:9.2f}% "
              f"{loss['taken']:8d} {per_sample_us:12.3f}")
    print("\n说明: 单次采样us = (开启采样的CPU耗时 - 基线) / 实采样本数，"
          "含 Python 信号分发与树聚合的全部成本。")


if __name__ == "__main__":
    main()
