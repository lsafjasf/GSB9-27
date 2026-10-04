"""自适应采样仿真：输出采样率随时间变化与异常链路保留率数据。

运行：python3 simulate.py
产出：
  out/sampling_rate_over_time.csv   每秒采样率/保留明细
  out/anomaly_retention.csv         各阶段异常保留率
  out/consistency_check.txt         链级一致性断言结果
"""

import csv
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.adaptive_sampler import AdaptiveSampler


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


# 阶段：(名称, 持续秒, 每秒链路数, 异常比例, 每条链路 span 数上限)
PHASES = [
    ("low_traffic",      20,    5, 0.000, 4),
    ("traffic_spike",    20,  500, 0.001, 4),
    ("rare_anomaly",     20,  200, 0.005, 5),
    ("budget_exhausted", 20, 2000, 0.010, 3),
]

BUDGET_PER_SEC = 80.0
HARD_CAP_PER_SEC = 100.0
WINDOW_SEC = 3.0
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rng = random.Random(20261004)
    clock = FakeClock()
    sampler = AdaptiveSampler(
        budget_per_sec=BUDGET_PER_SEC,
        window_sec=WINDOW_SEC,
        min_sample_rate=0.001,
        hard_cap_per_sec=HARD_CAP_PER_SEC,
        clock=clock,
    )

    rate_rows = []
    phase_rows = []
    trace_counter = 0
    inconsistent = []
    total_spans = 0

    for phase_name, duration, rps, error_ratio, max_spans in PHASES:
        p_offered = p_errors = p_kept_normal = p_kept_errors = 0

        for _ in range(duration):
            sec_offered = sec_errors = sec_kept_normal = sec_kept_errors = 0

            for _ in range(rps):
                clock.advance(1.0 / rps)
                trace_counter += 1
                trace_id = f"trace-{trace_counter}"
                is_error = rng.random() < error_ratio
                spans = rng.randint(1, max_spans)

                # 一条链路的多个 span 必须得到同一决定
                decision = sampler.should_sample(trace_id, is_error=is_error)
                for _span in range(spans):
                    total_spans += 1
                    again = sampler.should_sample(trace_id, is_error=is_error)
                    if again != decision:
                        inconsistent.append(trace_id)

                sec_offered += 1
                sec_errors += is_error
                if decision:
                    if is_error:
                        sec_kept_errors += 1
                    else:
                        sec_kept_normal += 1

            p = sampler.sample_rate(clock.t + 1e-6)
            rate_rows.append({
                "second": len(rate_rows),
                "phase": phase_name,
                "offered": sec_offered,
                "errors_offered": sec_errors,
                "sample_rate": round(p, 6),
                "kept_normal": sec_kept_normal,
                "kept_errors": sec_kept_errors,
                "kept_total": sec_kept_normal + sec_kept_errors,
            })
            p_offered += sec_offered
            p_errors += sec_errors
            p_kept_normal += sec_kept_normal
            p_kept_errors += sec_kept_errors

        normal_offered = p_offered - p_errors
        phase_rows.append({
            "phase": phase_name,
            "offered": p_offered,
            "errors_offered": p_errors,
            "errors_kept": p_kept_errors,
            "error_retention_pct": round(
                100.0 * p_kept_errors / p_errors, 2) if p_errors else 100.0,
            "normal_offered": normal_offered,
            "normal_kept": p_kept_normal,
            "normal_retention_pct": round(
                100.0 * p_kept_normal / normal_offered, 4) if normal_offered else 100.0,
        })

    # ---- 写文件 ----------------------------------------------------------
    rate_path = os.path.join(OUT_DIR, "sampling_rate_over_time.csv")
    with open(rate_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rate_rows[0].keys()))
        w.writeheader()
        w.writerows(rate_rows)

    ret_path = os.path.join(OUT_DIR, "anomaly_retention.csv")
    with open(ret_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(phase_rows[0].keys()))
        w.writeheader()
        w.writerows(phase_rows)

    # 链级一致性断言
    assert not inconsistent, f"存在 {len(inconsistent)} 条链路决定不一致: {inconsistent[:5]}"
    cons_path = os.path.join(OUT_DIR, "consistency_check.txt")
    with open(cons_path, "w") as f:
        f.write(
            "链级一致性断言: PASS\n"
            f"检查链路数: {trace_counter}（共 {total_spans} 个 span）\n"
            "同一 trace_id 的所有 span 采样决定完全一致，不一致链路数: 0\n"
        )

    # ---- 控制台摘要 ------------------------------------------------------
    print(f"预算 {BUDGET_PER_SEC:.0f}/s，硬上限 {HARD_CAP_PER_SEC:.0f}/s，"
          f"滑动窗口 {WINDOW_SEC:.0f}s\n")
    print("阶段               到达    异常  异常保留  正常保留率  采样率(末秒)")
    for row, raw in zip(phase_rows, PHASES):
        last_p = rate_rows[-1]["sample_rate"] if raw == PHASES[-1] else None
        end_p = [r["sample_rate"] for r in rate_rows if r["phase"] == row["phase"]][-1]
        print(f"{row['phase']:<16} {row['offered']:>7} "
              f"{row['errors_offered']:>5}  {row['error_retention_pct']:>6.2f}%  "
              f"{row['normal_retention_pct']:>8.4f}%  p={end_p:.4f}")
    print(f"\n链级一致性断言: PASS（{trace_counter} 条链路 / {total_spans} 个 span，不一致 0）")
    print(f"输出: {rate_path}\n      {ret_path}\n      {cons_path}")


if __name__ == "__main__":
    main()
