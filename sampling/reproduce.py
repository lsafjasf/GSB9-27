"""复现用例：故障时段监控面板看不到错误样本。

场景：正常运行 10_000 条正常记录中混入故障窗口的 50 条错误，
采样率 10%。对比缺陷实现与修复实现面板上可见的错误数。
"""

from sampler_buggy import UniformSampler
from sampler import Record, Sampler, CATEGORY_ERROR, CATEGORY_NORMAL


def build_traffic():
    records = []
    for i in range(10_000):
        records.append(Record(record_id=f"req-{i}", category=CATEGORY_NORMAL))
    for i in range(50):  # 故障窗口的错误
        records.append(Record(record_id=f"err-{i}", category=CATEGORY_ERROR))
    return records


def main():
    records = build_traffic()
    errors = [r for r in records if r.category == CATEGORY_ERROR]

    buggy = UniformSampler(seed=42, rate=0.1)
    buggy_kept_errors = [r for r in errors if buggy.keep(r)]
    # 注意：缺陷版用同一个 rng 流，实际部署中正常记录也消耗随机数，
    # 这里为突出缺陷本质，直接对错误记录单独演示均匀丢弃。
    print(f"[缺陷实现] 故障窗口错误 {len(errors)} 条，"
          f"面板可见 {len(buggy_kept_errors)} 条 "
          f"（约 {len(buggy_kept_errors)/len(errors):.0%}，现场丢失）")

    fixed = Sampler(seed=42, normal_rate=0.1)
    result = fixed.sample(records)
    for category, stats in sorted(result.stats.items()):
        print(f"[修复实现] {category}: 总量={stats.total} "
              f"保留={stats.kept} 丢弃={stats.dropped} "
              f"采样率={stats.effective_rate:.2%}")
    estimate = result.normal_total_estimate()
    error = result.normal_estimate_error()
    print(f"[修复实现] 正常记录总量估计={estimate:.0f} "
          f"真实={result.stats[CATEGORY_NORMAL].total} "
          f"相对误差={error:+.2%}")


if __name__ == "__main__":
    main()
