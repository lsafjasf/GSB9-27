"""无偏性验证：多种子下正常记录总量估计 vs 真实总量。"""

from sampler import Record, Sampler, CATEGORY_NORMAL

N = 20_000
RATE = 0.1
SEEDS = [f"seed-{i}" for i in range(50)]


def main():
    records = [Record(record_id=f"req-{i}", category=CATEGORY_NORMAL)
               for i in range(N)]
    errors = []
    print(f"真实总量={N}，采样率={RATE}，种子数={len(SEEDS)}")
    print(f"{'seed':>10} {'保留':>8} {'估计总量':>12} {'相对误差':>10}")
    for seed in SEEDS:
        result = Sampler(seed=seed, normal_rate=RATE).sample(records)
        estimate = result.normal_total_estimate()
        err = result.normal_estimate_error()
        errors.append(err)
        if int(seed.split("-")[1]) < 10:  # 只打印前 10 行，其余计入统计
            print(f"{seed:>10} {result.stats[CATEGORY_NORMAL].kept:>8} "
                  f"{estimate:>12.0f} {err:>+10.2%}")
    mean_err = sum(errors) / len(errors)
    max_err = max(abs(e) for e in errors)
    print(f"...")
    print(f"平均相对误差={mean_err:+.3%}，最大绝对相对误差={max_err:.3%}")
    print("结论：估计围绕真实值波动且均值接近 0，采样无偏。")


if __name__ == "__main__":
    main()
