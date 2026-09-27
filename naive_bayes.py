"""朴素贝叶斯文本分类器（多项事件模型，纯标准库实现）。

数值稳定性：
- 所有概率在对数域计算：log P(c) + sum log P(x_i|c)，避免概率连乘下溢。
- Lidstone 平滑：P(x|c) = (count(x,c) + alpha) / (N_c + alpha * V)，
  未见特征取平滑后的非零概率，消除零概率问题。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

EPS = 1e-12


class MultinomialNaiveBayes:
    """多项朴素贝叶斯文本分类器。

    参数
    ----
    alpha:
        Lidstone 平滑参数，必须 >= 0。alpha=1 为拉普拉斯平滑（默认）；
        0 < alpha < 1（如 0.1）为更弱的 Lidstone 平滑；alpha=0 不平滑，
        未见特征产生 -inf 对数概率。
    class_prior:
        "empirical"：按训练集类别频率估计先验（默认）；
        "uniform"：各类先验均为 1/K（完全忽略类别不平衡）；
        或传入 {类别: 概率} 自定义（概率之和须为 1）。
    """

    def __init__(
        self,
        alpha: float = 1.0,
        class_prior: str | Mapping[str, float] = "empirical",
    ) -> None:
        if alpha < 0:
            raise ValueError("alpha 必须 >= 0")
        if isinstance(class_prior, str) and class_prior not in ("empirical", "uniform"):
            raise ValueError("class_prior 须为 'empirical'、'uniform' 或概率字典")
        self.alpha = float(alpha)
        self.class_prior = class_prior
        self.vocabulary: Dict[str, int] = {}
        self.classes: List[str] = []
        self.log_prior: Dict[str, float] = {}
        self.log_feature_prob: Dict[str, Dict[str, float]] = {}
        self.feature_counts: Dict[str, Counter] = {}
        self.feature_totals: Dict[str, int] = {}
        self.class_doc_counts: Dict[str, int] = {}

    @staticmethod
    def _normalize_doc(doc) -> Counter:
        if isinstance(doc, Mapping):
            return Counter({str(k): int(v) for k, v in doc.items() if int(v) != 0})
        if isinstance(doc, str):
            raise TypeError("文档须为 token 列表/可迭代对象 或 {token: 次数} 字典，不接受裸字符串")
        return Counter(str(tok) for tok in doc)

    def fit(self, X: Sequence, y: Sequence[str]) -> "MultinomialNaiveBayes":
        if len(X) != len(y):
            raise ValueError("X 与 y 长度必须一致")
        class_doc_counts: Dict[str, int] = defaultdict(int)
        feature_counts: Dict[str, Counter] = defaultdict(Counter)
        for doc, label in zip(X, y):
            label = str(label)
            class_doc_counts[label] += 1
            feature_counts[label].update(self._normalize_doc(doc))

        self.classes = sorted(class_doc_counts)
        self.class_doc_counts = dict(class_doc_counts)
        self.feature_counts = {c: feature_counts[c] for c in self.classes}
        self.feature_totals = {c: sum(feature_counts[c].values()) for c in self.classes}

        vocab: set = set()
        for counter in feature_counts.values():
            vocab.update(counter)
        self.vocabulary = {tok: i for i, tok in enumerate(sorted(vocab))}
        vocab_size = len(self.vocabulary)

        n_docs = len(y)
        k_classes = len(self.classes)
        priors: Dict[str, float] = {}
        if isinstance(self.class_prior, Mapping):
            given = {str(k): float(v) for k, v in self.class_prior.items()}
            missing = set(self.classes) - set(given)
            if missing:
                raise ValueError(f"自定义先验缺少类别: {sorted(missing)}")
            if any(v < 0 for v in given.values()):
                raise ValueError("先验概率不能为负")
            total = sum(given[c] for c in self.classes)
            if abs(total - 1.0) > 1e-9:
                raise ValueError(f"自定义先验之和须为 1，当前为 {total}")
            priors = {c: given[c] for c in self.classes}
        elif self.class_prior == "uniform":
            priors = {c: 1.0 / k_classes for c in self.classes}
        else:
            priors = {c: class_doc_counts[c] / n_docs for c in self.classes}
        self.log_prior = {c: math.log(priors[c]) for c in self.classes}

        alpha = self.alpha
        self.log_feature_prob = {}
        for c in self.classes:
            total = self.feature_totals[c]
            denom = total + alpha * vocab_size
            if vocab_size == 0:
                # 训练集中没有任何特征：特征无任何信息量，似然一律记 0（=log 1），
                # 退化为只按先验分类。
                self.log_feature_prob[c] = {}
                continue
            table = {}
            if denom == 0:
                # alpha=0 且该类一个特征都没有：MLE 无法定义，
                # 任何特征概率都记为 -inf（只有空文档可能属于该类）。
                table = {token: float("-inf") for token in self.vocabulary}
            else:
                for token in self.vocabulary:
                    count = feature_counts[c].get(token, 0) + alpha
                    table[token] = math.log(count / denom) if count > 0 else float("-inf")
            self.log_feature_prob[c] = table
        return self

    def _log_likelihood_features(self, label: str, doc_counts: Counter) -> float:
        if not self.vocabulary:
            return 0.0
        table = self.log_feature_prob[label]
        alpha = self.alpha
        denom = self.feature_totals[label] + alpha * len(self.vocabulary)
        score = 0.0
        unseen_log = math.log(alpha / denom) if alpha > 0 else float("-inf")
        for token, freq in doc_counts.items():
            score += freq * table.get(token, unseen_log)
        return score

    def log_scores(self, doc) -> Dict[str, float]:
        """返回各类别的 log P(c|d) 未归一化对数分（log 先验 + log 似然之和）。"""
        if not self.classes:
            raise RuntimeError("分类器尚未训练")
        doc_counts = self._normalize_doc(doc)
        return {
            c: self.log_prior[c] + self._log_likelihood_features(c, doc_counts)
            for c in self.classes
        }

    def predict(self, doc) -> Tuple[str, Dict[str, float]]:
        """返回 (预测类别, 各类别对数分)。分数相同按类别名字典序取较小者，保证确定性。"""
        scores = self.log_scores(doc)
        best = min(scores, key=lambda c: (-scores[c], c))
        return best, scores


def demo() -> None:
    train_X = [
        ["buy", "cheap", "money"],
        ["cheap", "money", "deal"],
        ["meeting", "project", "team"],
        ["project", "team", "schedule"],
    ]
    train_y = ["spam", "spam", "ham", "ham"]
    clf = MultinomialNaiveBayes(alpha=1.0, class_prior="uniform").fit(train_X, train_y)
    label, scores = clf.predict(["cheap", "project"])
    print(f"预测类别: {label}")
    for c in sorted(scores):
        print(f"  {c}: {scores[c]:.6f}")


if __name__ == "__main__":
    demo()
