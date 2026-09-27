"""泄露面量化演示：确定性标签的频率分析攻击。

模型：攻击者拿到整列标签（数据库泄露），并且从公开渠道知道该字段的
取值分布（如姓氏/城市/诊断编码的公开统计）。由于标签是确定性的，
攻击者把「观测到的标签频率排名」与「公开分布的频率排名」对齐，即可
高置信度地还原高频明文。

运行：python3 leakage_demo.py
"""

import random
from collections import Counter


def zipf_weights(vocab: int, s: float) -> list[float]:
    return [1.0 / (rank**s) for rank in range(1, vocab + 1)]


def sample(weights: list[float], n: int, rng: random.Random) -> list[int]:
    return rng.choices(range(len(weights)), weights=weights, k=n)


def rank_attack(observed: Counter, weights: list[float], top_k: int) -> float:
    """按频率排名对齐，返回 top_k 高频值的还原准确率。"""
    true_ranking = sorted(range(len(weights)), key=lambda i: -weights[i])[:top_k]
    obs_ranking = [value for value, _ in observed.most_common(top_k)]
    hits = sum(1 for t, o in zip(true_ranking, obs_ranking) if t == o)
    return hits / top_k


def main() -> None:
    rng = random.Random(20260928)
    vocab, skew, top_k = 20_000, 1.2, 100
    weights = zipf_weights(vocab, skew)
    top1_mass = weights[0] / sum(weights)
    top100_mass = sum(weights[:100]) / sum(weights)
    print(f"分布: Zipf(s={skew})，取值空间 {vocab}，"
          f"最高频值占比 {top1_mass:.2%}，top-100 合计占比 {top100_mass:.2%}")
    print()
    header = f"{'记录数 n':>12} | {'top-100 还原准确率':>18} | {'唯一标签占比(安全侧)':>20}"
    print(header)
    print("-" * len(header))
    for n in (1_000, 10_000, 100_000, 1_000_000):
        observed = Counter(sample(weights, n, rng))
        acc = rank_attack(observed, weights, top_k)
        singletons = sum(1 for c in observed.values() if c == 1) / len(observed)
        print(f"{n:>12,} | {acc:>18.1%} | {singletons:>20.1%}")
    print()
    print("解读：n 越大，标签频率越贴近真实分布，排名对齐攻击越准；")
    print("只出现 1 次的取值无法被频率对齐，但仍可被等值关联（同一值跨表/跨时间追踪）。")


if __name__ == "__main__":
    main()
