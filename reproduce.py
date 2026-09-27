"""复现用例：旧采样器丢失错误现场 vs 修复版；并输出无偏性实验数据。

运行: python3 reproduce.py
输出:
  控制台  —— 故障窗口复现、四类场景摘要、无偏性汇总
  unbiasedness.csv —— 蒙特卡洛逐次实验数据（含估计误差）
"""

from __future__ import annotations

import csv
import math
import random
from typing import List, Sequence, Tuple

from legacy_sampler import LegacySampler
from sampler import (
    CATEGORIES,
    CATEGORY_ERROR,
    CATEGORY_NORMAL,
    CATEGORY_OVER_THRESHOLD,
    Record,
    Sampler,
)

CSV_PATH = "unbiasedness.csv"
NORMAL_RATE = 0.1
THRESHOLD_MS = 1000.0
EXP_TRIALS = 300
EXP_NORMAL_COUNT = 10000
EXP_SEED_BASE = 20260927


def generate_records(n_normal: int, n_error: int, n_over: int, *, seed: int) -> List[Record]:
    """生成确定的合成流量：正常延迟集中在阈值内，错误/超阈值为少数派。"""
    rng = random.Random(seed)
    records: List[Record] = []
    next_id = 0
    for _ in range(n_normal):
        records.append(Record(next_id, latency_ms=rng.uniform(5.0, 300.0), is_error=False))
        next_id += 1
    for _ in range(n_error):
        records.append(
            Record(next_id, latency_ms=rng.uniform(5.0, 600.0), is_error=True)
        )
        next_id += 1
    for _ in range(n_over):
        records.append(
            Record(next_id, latency_ms=rng.uniform(1001.0, 5000.0), is_error=False)
        )
        next_id += 1
    rng.shuffle(records)
    return records


def category_counts(records: Sequence[Record], sampler: Sampler) -> Tuple[int, int, int]:
    errors = sum(1 for r in records if sampler.classify(r) == CATEGORY_ERROR)
    overs = sum(1 for r in records if sampler.classify(r) == CATEGORY_OVER_THRESHOLD)
    normals = sum(1 for r in records if sampler.classify(r) == CATEGORY_NORMAL)
    return errors, overs, normals


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def print_result(result) -> None:
    for line in result.report_lines():
        print("  " + line)


def repro_fault_window() -> None:
    section("场景 A：故障窗口复现（正常 2000 / 错误 20 / 超阈值 5，rate=10%）")
    records = generate_records(2000, 20, 5, seed=41)

    legacy_kept, _ = LegacySampler(NORMAL_RATE, seed=3).sample(records)
    legacy_errors = sum(1 for r in legacy_kept if r.is_error)
    legacy_overs = sum(1 for r in legacy_kept if r.latency_ms > THRESHOLD_MS)
    print("旧版采样器（所有记录按同一比例丢弃）:")
    print(
        f"  保留 {len(legacy_kept)} 条，其中错误 {legacy_errors} 条、超阈值 {legacy_overs} 条"
        f"  -> 错误保留率 {legacy_errors / 20:.0%}"
    )
    assert legacy_errors == 0 and legacy_overs == 0
    print("  !! 面板在故障时段看不到任何错误样本，现场丢失（问题复现）")

    result = Sampler(NORMAL_RATE, threshold_ms=THRESHOLD_MS, seed=3).sample(records)
    print("修复版采样器（错误/超阈值全量保留，仅正常记录采样）:")
    print_result(result)
    kept_errors = result.stats[CATEGORY_ERROR].kept
    kept_overs = result.stats[CATEGORY_OVER_THRESHOLD].kept
    assert kept_errors == 20 and kept_overs == 5
    print(f"  -> 错误保留 {kept_errors}/20，超阈值保留 {kept_overs}/5，现场完整")


def run_scenario(name: str, n_normal: int, n_error: int, n_over: int, rate: float, seed: int) -> None:
    section(f"场景 {name}：正常 {n_normal} / 错误 {n_error} / 超阈值 {n_over}，rate={rate:.0%}")
    records = generate_records(n_normal, n_error, n_over, seed=seed)
    sampler = Sampler(rate, threshold_ms=THRESHOLD_MS, seed=seed + 1)
    result = sampler.sample(records)
    print_result(result)

    true_errors, true_overs, true_normals = category_counts(records, sampler)
    assert result.stats[CATEGORY_ERROR].kept == true_errors
    assert result.stats[CATEGORY_OVER_THRESHOLD].kept == true_overs
    total_hat = result.estimated_count()
    true_total = true_errors + true_overs + true_normals
    if rate > 0.0:
        rel_err = abs(total_hat - true_total) / true_total
        print(f"  总量真值={true_total}，逆概率估计={total_hat:.1f}，相对误差={rel_err:.4%}")
    else:
        assert result.stats[CATEGORY_NORMAL].kept == 0
        assert result.stats[CATEGORY_NORMAL].dropped == true_normals
        print(f"  rate=0：正常记录全部丢弃（dropped={true_normals}），总量估计不可用（p=0 无定义）")
    print(f"  丢弃计数: {result.drop_counts()}")


def reproducibility_demo() -> None:
    section("可复现性：同种子同序列两次采样，保留集合必须完全一致")
    records = generate_records(3000, 50, 20, seed=7)
    r1 = Sampler(NORMAL_RATE, seed=12345).sample(records)
    r2 = Sampler(NORMAL_RATE, seed=12345).sample(records)
    r3 = Sampler(NORMAL_RATE, seed=12346).sample(records)
    print(f"  seed=12345 第一次保留 {len(r1.kept)} 条")
    print(f"  seed=12345 第二次保留 {len(r2.kept)} 条，保留集合一致: {r1.kept_id_set() == r2.kept_id_set()}")
    print(f"  seed=12346 另一次保留 {len(r3.kept)} 条，不同种子集合不同: {r1.kept_id_set() != r3.kept_id_set()}")
    assert r1.kept_id_set() == r2.kept_id_set()
    assert r1.kept_id_set() != r3.kept_id_set()


def run_unbiasedness() -> None:
    section(f"无偏性实验：{EXP_TRIALS} 次独立试验，每次 {EXP_NORMAL_COUNT} 条正常记录，p={NORMAL_RATE}")
    trials = []
    true_count = EXP_NORMAL_COUNT
    true_latency_sum = 0.0
    for t in range(EXP_TRIALS):
        records = generate_records(true_count, 0, 0, seed=EXP_SEED_BASE + t)
        true_latency_sum = sum(r.latency_ms for r in records)
        result = Sampler(NORMAL_RATE, seed=EXP_SEED_BASE + 10_000 + t).sample(records)
        count_hat = result.estimated_count(CATEGORY_NORMAL)
        latency_hat = result.estimated_latency_sum(CATEGORY_NORMAL)
        trials.append(
            {
                "trial": t,
                "rate": NORMAL_RATE,
                "true_count": true_count,
                "estimated_count": round(count_hat, 3),
                "count_rel_error": (count_hat - true_count) / true_count,
                "true_latency_sum": round(true_latency_sum, 3),
                "estimated_latency_sum": round(latency_hat, 3),
                "latency_rel_error": (latency_hat - true_latency_sum) / true_latency_sum,
                "kept_normal": result.stats[CATEGORY_NORMAL].kept,
            }
        )

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(trials[0].keys()))
        writer.writeheader()
        writer.writerows(trials)

    n = len(trials)
    count_hats = [t["estimated_count"] for t in trials]
    count_rel = [t["count_rel_error"] for t in trials]
    lat_rel = [t["latency_rel_error"] for t in trials]
    mean_count = sum(count_hats) / n
    mean_bias_rel = (mean_count - true_count) / true_count
    rmse_count = math.sqrt(sum((x - true_count) ** 2 for x in count_hats) / n)

    def sample_se(values: List[float]) -> float:
        mean = sum(values) / len(values)
        return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))

    se_count_observed = sample_se(count_rel)
    se_count_theory = math.sqrt((1.0 - NORMAL_RATE) / (NORMAL_RATE * true_count))
    se_lat_observed = sample_se(lat_rel)
    max_abs_count_rel = max(abs(v) for v in count_rel)

    print(f"  总量真值                 : {true_count}")
    print(f"  估计量均值               : {mean_count:.3f}")
    print(f"  相对偏差（均值-真值）    : {mean_bias_rel:+.4%}   <- 越接近 0 越无偏")
    print(f"  RMSE（计数）             : {rmse_count:.3f} ({rmse_count / true_count:.4%})")
    print(f"  单次相对误差标准差（实测）: {se_count_observed:.4%}")
    print(f"  单次相对误差标准差（理论）: {se_count_theory:.4%}   <- sqrt((1-p)/(p*N))")
    print(f"  单次最大绝对相对误差      : {max_abs_count_rel:.4%}")
    print(f"  latency 和相对偏差（均值）: {sum(lat_rel) / len(lat_rel):+.4%}（标准差 {se_lat_observed:.4%}）")
    print(f"  逐次数据已写入 {CSV_PATH}")
    assert abs(mean_bias_rel) < 0.005, "蒙特卡洛均值偏差超阈值，采样不满足无偏性"


def main() -> None:
    repro_fault_window()
    run_scenario("B 全是错误", 0, 500, 0, NORMAL_RATE, seed=101)
    run_scenario("C 全正常", 5000, 0, 0, NORMAL_RATE, seed=102)
    run_scenario("D 流量突增十倍", 50000, 200, 80, NORMAL_RATE, seed=103)
    run_scenario("E 采样率为零", 2000, 30, 10, 0.0, seed=104)
    reproducibility_demo()
    run_unbiasedness()
    print()
    print("全部场景通过。")


if __name__ == "__main__":
    main()
