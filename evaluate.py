"""与人工标注对拍：pairwise 精确率/召回率/F1、漏并率、误并率。

用法: python3 evaluate.py [labeled.json]
  无参数时先用 datagen 生成 data/labeled_failures.json（若不存在）再评估。
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

from datagen import generate_labeled
from failcluster import FailureRecord, cluster_failures

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "labeled_failures.json")


def pairwise_metrics(pred_groups: list[set[str]], true_groups: list[set[str]]) -> dict:
    """基于样本对的指标。

    - 准确率(precision): 被并在一起的样本对中，标注也确实同类的比例
    - 召回率(recall)    : 标注同类的样本对中，被成功并在一起的比例
    - 漏并率            : 1 - recall（应并未并）
    - 误并率            : 被并在一起的样本对中，标注不同类的比例 = 1 - precision
    """

    def pairs(groups: list[set[str]]) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for g in groups:
            members = sorted(g)
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    out.add((members[i], members[j]))
        return out

    pred_pairs, true_pairs = pairs(pred_groups), pairs(true_groups)
    tp = len(pred_pairs & true_pairs)
    precision = tp / len(pred_pairs) if pred_pairs else 1.0
    recall = tp / len(true_pairs) if true_pairs else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "miss_merge_rate": 1.0 - recall,
        "wrong_merge_rate": 1.0 - precision,
        "pred_pairs": len(pred_pairs),
        "true_pairs": len(true_pairs),
        "tp_pairs": tp,
    }


def evaluate(records: list[dict]) -> dict:
    frs = [FailureRecord(id=r["id"], text=r["text"]) for r in records]
    clusters = cluster_failures(frs)
    pred_groups = [{m.id for m in c.members} for c in clusters]
    by_label: dict[str, set[str]] = defaultdict(set)
    for r in records:
        by_label[r["label"]].add(r["id"])
    metrics = pairwise_metrics(pred_groups, list(by_label.values()))
    metrics["n_records"] = len(records)
    metrics["n_true_labels"] = len(by_label)
    metrics["n_pred_clusters"] = len(clusters)
    return metrics


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DATA_PATH
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = generate_labeled()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        print(f"已生成标注数据: {path} ({len(data)} 条, {len({d['label'] for d in data})} 类)")
    with open(path, encoding="utf-8") as fh:
        records = json.load(fh)
    m = evaluate(records)
    print(f"\n对拍结果（{m['n_records']} 条，人工标注 {m['n_true_labels']} 类，"
          f"聚出 {m['n_pred_clusters']} 组）")
    print(f"  准确率 precision   : {m['precision']:.4f}")
    print(f"  召回率 recall      : {m['recall']:.4f}")
    print(f"  F1                 : {m['f1']:.4f}")
    print(f"  漏并率 miss-merge  : {m['miss_merge_rate']:.4f}  (应并未并的样本对占比)")
    print(f"  误并率 wrong-merge : {m['wrong_merge_rate']:.4f}  (不应并却被并的占比)")
    print(f"  样本对: 预测同组 {m['pred_pairs']} / 标注同组 {m['true_pairs']} / 命中 {m['tp_pairs']}")
    return 0 if m["f1"] >= 0.99 else 1


if __name__ == "__main__":
    raise SystemExit(main())
