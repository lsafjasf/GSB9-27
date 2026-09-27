"""演示：不平衡数据上的分层切分 + 分布对比数据输出。

运行：python3 demo.py
"""

import random
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from stratified_split import distribution_report, format_report, stratified_split


def main():
    rng = random.Random(20260928)
    # 模拟不平衡数据集：头部类 2000，尾部稀有类仅 3 个样本。
    class_sizes = {"head": 2000, "mid": 400, "tail": 40, "rare": 3}
    samples, labels = [], []
    for label, n in class_sizes.items():
        for i in range(n):
            samples.append(f"{label}_{i}")
            labels.append(label)
    rng.shuffle(samples)  # 打乱输入顺序，验证顺序无关
    labels = [s.rsplit("_", 1)[0] for s in samples]

    train, val = stratified_split(samples, labels, train_ratio=0.8, seed=42, min_per_side=1)
    rep = distribution_report(train, val, labels=lambda s: s.rsplit("_", 1)[0], train_ratio=0.8)
    print(format_report(rep))

    # 可复现性自检：换输入顺序再切一次，结果必须完全一致。
    idx = list(range(len(samples)))
    rng.shuffle(idx)
    t2, v2 = stratified_split([samples[i] for i in idx], [labels[i] for i in idx],
                              train_ratio=0.8, seed=42, min_per_side=1)
    assert (t2, v2) == (train, val), "reproducibility check failed"
    print("\nreproducibility check: OK (input order shuffled, identical split)")


if __name__ == "__main__":
    main()
