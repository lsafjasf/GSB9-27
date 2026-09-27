"""stratified_split 自测：可复现性、顺序无关、边界用例、比例偏差。"""

import random
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stratified_split import distribution_report, stratified_split


def make_data(class_sizes, start=0):
    """生成 [(sample, label), ...]，样本为全局唯一整数。"""
    data, n = [], start
    for ci, size in enumerate(class_sizes):
        for _ in range(size):
            data.append((n, f"class_{ci}"))
            n += 1
    return data


class TestReproducibility(unittest.TestCase):
    def setUp(self):
        self.data = make_data([500, 200, 50, 10, 3, 1])
        self.samples = [s for s, _ in self.data]
        self.labels = [l for _, l in self.data]

    def test_same_seed_same_result(self):
        a = stratified_split(self.samples, self.labels, 0.8, seed=42)
        b = stratified_split(self.samples, self.labels, 0.8, seed=42)
        self.assertEqual(a, b)

    def test_input_order_irrelevant(self):
        train_ref, val_ref = stratified_split(self.samples, self.labels, 0.8, seed=42)
        for trial in range(20):
            idx = list(range(len(self.samples)))
            random.Random(trial).shuffle(idx)
            s = [self.samples[i] for i in idx]
            l = [self.labels[i] for i in idx]
            train, val = stratified_split(s, l, 0.8, seed=42)
            self.assertEqual(train, train_ref)
            self.assertEqual(val, val_ref)

    def test_different_seed_different_result(self):
        a = stratified_split(self.samples, self.labels, 0.8, seed=1)
        b = stratified_split(self.samples, self.labels, 0.8, seed=2)
        self.assertNotEqual(a, b)

    def test_no_sample_loss_or_duplication(self):
        train, val = stratified_split(self.samples, self.labels, 0.75, seed=7)
        self.assertEqual(sorted(train + val), sorted(self.samples))
        self.assertEqual(len(set(train) & set(val)), 0)


class TestRatioAndMinSamples(unittest.TestCase):
    def test_ratio_close_to_target(self):
        data = make_data([1000, 500, 100, 40])
        samples = [s for s, _ in data]
        labels = [l for _, l in data]
        train, val = stratified_split(samples, labels, 0.8, seed=0)
        label_of = {s: l for s, l in data}
        rep = distribution_report(train, val, labels=lambda s: label_of[s], train_ratio=0.8)
        self.assertLessEqual(rep["overall"]["abs_deviation"], 0.01)
        for row in rep["per_class"].values():
            self.assertLessEqual(row["abs_deviation"], 0.05)

    def test_min_per_side_guaranteed_when_feasible(self):
        data = make_data([100, 50, 10, 4, 2])
        samples = [s for s, _ in data]
        labels = [l for _, l in data]
        train, val = stratified_split(samples, labels, 0.7, seed=3, min_per_side=2)
        label_of = {s: l for s, l in data}
        ct = Counter(label_of[s] for s in train)
        cv = Counter(label_of[s] for s in val)
        for label, n in zip((f"class_{i}" for i in range(5)), [100, 50, 10, 4, 2]):
            if n >= 4:  # n >= 2*min_per_side：两侧都必须 >= 2
                self.assertGreaterEqual(ct[label], 2, label)
                self.assertGreaterEqual(cv[label], 2, label)
            else:  # 稀有类：整类进训练集
                self.assertEqual(ct[label], n, label)
                self.assertEqual(cv[label], 0, label)

    def test_pure_random_split_can_lose_rare_class_but_stratified_cannot(self):
        # 对照：纯随机切分下小类可能整类落进一侧；分层切分保证两侧都有。
        data = make_data([90, 10])
        samples = [s for s, _ in data]
        labels = [l for _, l in data]
        train, val = stratified_split(samples, labels, 0.9, seed=0)
        label_of = {s: l for s, l in data}
        cv = Counter(label_of[s] for s in val)
        self.assertGreaterEqual(cv["class_1"], 1)


class TestEdgeCases(unittest.TestCase):
    def test_single_class(self):
        samples = list(range(100))
        labels = ["only"] * 100
        train, val = stratified_split(samples, labels, 0.8, seed=0)
        self.assertEqual(len(train), 80)
        self.assertEqual(len(val), 20)

    def test_more_classes_than_samples(self):
        # 5 个类、3 个样本：部分类必然为空，切分不应报错。
        samples = [10, 20, 30]
        labels = ["a", "b", "c"]
        train, val = stratified_split(samples, labels, 0.6, seed=0)
        self.assertEqual(sorted(train + val), samples)
        self.assertEqual(len(train), 3)  # 每类仅 1 个样本，稀有策略整类进训练集
        self.assertEqual(len(val), 0)

    def test_one_sample_per_class(self):
        samples = list(range(50))
        labels = [f"c{i}" for i in range(50)]
        train, val = stratified_split(samples, labels, 0.8, seed=0)
        self.assertEqual(len(train), 50)  # 全部稀有类 -> 全进训练集
        self.assertEqual(len(val), 0)
        self.assertEqual(sorted(train), samples)

    def test_duplicate_samples(self):
        # 大量重复样本：结果按多重集合比较，仍须可复现且守恒。
        samples = [1, 1, 1, 2, 2, 3, 3, 3, 3, 4] * 10
        labels = ["even" if s % 2 == 0 else "odd" for s in samples]
        t1, v1 = stratified_split(samples, labels, 0.7, seed=9)
        shuffled = samples[:]
        random.Random(1).shuffle(shuffled)
        sl = ["even" if s % 2 == 0 else "odd" for s in shuffled]
        t2, v2 = stratified_split(shuffled, sl, 0.7, seed=9)
        self.assertEqual(t1, t2)
        self.assertEqual(v1, v2)
        self.assertEqual(Counter(t1) + Counter(v1), Counter(samples))
        # 比例守恒：odd 70 个 -> 训练 49；even 30 个 -> 训练 21
        self.assertEqual(len(t1), 70)
        self.assertEqual(len(v1), 30)

    def test_callable_label_key(self):
        samples = list(range(40))
        train, val = stratified_split(samples, labels=lambda s: s % 4, train_ratio=0.75, seed=5)
        rep = distribution_report(train, val, labels=lambda s: s % 4, train_ratio=0.75)
        # 每类 10 个、比例 0.75：最大余数法下每类 7 或 8，全局恰好 30/40。
        for row in rep["per_class"].values():
            self.assertIn(row["train_count"], (7, 8))
            self.assertEqual(row["train_count"] + row["val_count"], 10)
        self.assertEqual(rep["overall"]["train_count"], 30)
        self.assertEqual(rep["overall"]["abs_deviation"], 0.0)

    def test_empty_input(self):
        train, val = stratified_split([], [], 0.8, seed=0)
        self.assertEqual((train, val), ([], []))

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            stratified_split([1], [1], 1.0)
        with self.assertRaises(ValueError):
            stratified_split([1, 2], [1], 0.5)
        with self.assertRaises(ValueError):
            stratified_split([1], [1], 0.5, rare_policy="nope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
