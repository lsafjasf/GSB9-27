"""演示：不均衡数据集上的分层切分与分布对比报告。

运行: python3 demo.py
"""

import random

from stratified_split import (
    stratified_split,
    distribution_report,
    format_report,
)


def main():
    rng = random.Random(42)
    # 类别极不均衡：cat 2000、dog 300、bird 30、rare_frog 2。
    items = []
    for cls, n in (("cat", 2000), ("dog", 300), ("bird", 30), ("rare_frog", 2)):
        items += [(cls, i) for i in range(n)]
    rng.shuffle(items)

    ratios = {"train": 0.8, "val": 0.2}
    key_fn = lambda x: x[0]
    splits = stratified_split(items, key_fn, ratios, seed=2024, min_per_split=1)
    report = distribution_report(items, key_fn, ratios, splits)
    print(format_report(report))


if __name__ == "__main__":
    main()
