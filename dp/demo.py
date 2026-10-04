"""演示脚本：生成预算消耗数据、不同预算下的精度数据、小分组抑制结果。

运行：python3 demo.py
输出：终端打印 + 当前目录下 budget_ledger.csv / accuracy_vs_epsilon.csv /
      suppression_report.csv
"""

import csv
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dp_lib import BudgetExhausted, DPAggregator, PrivacyAccountant

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def demo_budget_ledger():
    print("=" * 78)
    print("场景 1：多轮查询共享预算（总预算 epsilon=1.0）")
    print("=" * 78)
    acc = PrivacyAccountant(1.0, random.Random(42))
    agg = DPAggregator(acc)
    data = [random.Random(7).randint(0, 100) for _ in range(1000)]

    plan = [("count_all", 0.3), ("sum_bounded", 0.3), ("mean_bounded", 0.2),
            ("group_counts", 0.2)]
    agg.count(data, epsilon=plan[0][1], name=plan[0][0])
    agg.sum(data, epsilon=plan[1][1], bound=100.0, name=plan[1][0])
    agg.mean(data, epsilon=plan[2][1], bound=100.0, n=len(data), name=plan[2][0])
    agg.group_counts(data, key_fn=lambda x: x // 25, epsilon=plan[3][1],
                     threshold=5.0, name=plan[3][0])

    print(acc.table())

    # 预算耗尽：剩余为 0，再查即拒绝
    try:
        agg.count(data, epsilon=0.01, name="over_budget")
    except BudgetExhausted as e:
        print(f"\n[预算耗尽] 第 5 轮查询被拒绝: {e}")

    path = os.path.join(OUT_DIR, "budget_ledger.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["round", "query", "epsilon_used", "epsilon_remaining", "noise_scale"])
        for e in acc.ledger:
            w.writerow([e.round_no, e.query_name, f"{e.epsilon_used:.6f}",
                        f"{e.epsilon_remaining:.6f}", f"{e.noise_scale:.6f}"])
    print(f"预算数据已写入: {path}\n")


def demo_accuracy_vs_epsilon():
    print("=" * 78)
    print("场景 2：不同预算下的结果精度（count 查询，n=1000，敏感度=1，500 次重复）")
    print("=" * 78)
    n = 1000
    data = [0] * n
    epsilons = [0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
    trials = 500

    rows = []
    print(f"{'epsilon':>8} | {'noise_scale':>11} | {'MAE':>8} | {'RMSE':>8} | "
          f"{'95%误差上界':>10} | {'理论95%上界':>10}")
    print("-" * 78)
    for eps in epsilons:
        acc = PrivacyAccountant(eps * trials, random.Random(2024))
        agg = DPAggregator(acc)
        errs = []
        for _ in range(trials):
            r = agg.count(data, epsilon=eps, name="count")
            errs.append(r.value - r.true_value)
        mae = statistics.fmean(abs(e) for e in errs)
        rmse = (statistics.fmean(e * e for e in errs)) ** 0.5
        p95 = sorted(abs(e) for e in errs)[int(0.95 * trials) - 1]
        theory95 = 3.0 / eps  # Laplace 95% 分位: scale * ln(20) ≈ 3*scale
        print(f"{eps:8.2f} | {1/eps:11.4f} | {mae:8.3f} | {rmse:8.3f} | "
              f"{p95:10.3f} | {theory95:10.3f}")
        rows.append([eps, 1 / eps, f"{mae:.4f}", f"{rmse:.4f}",
                     f"{p95:.4f}", f"{theory95:.4f}"])

    path = os.path.join(OUT_DIR, "accuracy_vs_epsilon.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epsilon", "noise_scale", "MAE", "RMSE",
                    "empirical_95pct_error", "theoretical_95pct_bound"])
        w.writerows(rows)
    print(f"\n精度数据已写入: {path}")
    print("结论：epsilon 越小 -> 噪声 scale 越大 -> 误差越大；预算与精度一一对应。\n")


def demo_suppression():
    print("=" * 78)
    print("场景 3：小分组抑制（阈值 kappa=10）")
    print("=" * 78)
    acc = PrivacyAccountant(1.0, random.Random(99))
    agg = DPAggregator(acc)
    groups = {"北京": 500, "上海": 420, "广州": 60, "小县城A": 7, "小县城B": 3}
    records = [g for g, c in groups.items() for _ in range(c)]
    res = agg.group_counts(records, key_fn=lambda x: x, epsilon=1.0, threshold=10.0)

    print(f"抑制规则: noisy_count < {res.suppression_threshold} 的分组不下发")
    print(f"{'分组':<8} | {'真实计数':>8} | {'加噪计数':>10} | 是否发布")
    print("-" * 55)
    for g in sorted(groups):
        published = g in res.noisy_counts
        noisy = f"{res.noisy_counts[g]:.2f}" if published else "(已抑制)"
        print(f"{g:<8} | {groups[g]:>8} | {noisy:>10} | {'发布' if published else '抑制'}")
    print(f"\n被抑制分组: {res.suppressed}")
    print(f"本轮消耗 epsilon={res.epsilon_used}, 剩余={acc.epsilon_remaining:.4f}")

    path = os.path.join(OUT_DIR, "suppression_report.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group", "true_count", "noisy_count", "published",
                    "suppression_threshold"])
        for g in sorted(groups):
            published = g in res.noisy_counts
            w.writerow([g, groups[g],
                        f"{res.noisy_counts[g]:.4f}" if published else "",
                        published, res.suppression_threshold])
    print(f"抑制结果已写入: {path}\n")


if __name__ == "__main__":
    demo_budget_ledger()
    demo_accuracy_vs_epsilon()
    demo_suppression()
