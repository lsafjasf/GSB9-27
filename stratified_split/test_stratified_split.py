"""stratified_split 的自测，仅使用标准库 unittest。

运行: python3 -m unittest test_stratified_split -v
"""

import random
import unittest

from stratified_split import (
    stratified_split,
    distribution_report,
    _allocate_class,
)


RATIOS = {"train": 0.8, "val": 0.2}


def make_skewed(seed=42):
    """构造类别极不均衡的数据集：A 类 1000 个，B 类 100 个，C 类 10 个。"""
    rng = random.Random(seed)
    items = []
    for cls, n in (("A", 1000), ("B", 100), ("C", 10)):
        for i in range(n):
            items.append("{}-{:04d}".format(cls, i))
    rng.shuffle(items)
    return items


class TestReproducibility(unittest.TestCase):
    def test_same_input_same_output(self):
        items = make_skewed()
        s1 = stratified_split(items, key_fn=lambda x: x.split("-")[0],
                              ratios=RATIOS, seed=7)
        s2 = stratified_split(items, key_fn=lambda x: x.split("-")[0],
                              ratios=RATIOS, seed=7)
        self.assertEqual(s1, s2)

    def test_input_order_irrelevant(self):
        items = make_skewed()
        key = lambda x: x.split("-")[0]
        base = stratified_split(items, key, RATIOS, seed=7)
        rng = random.Random(123)
        for _ in range(20):
            shuffled = items[:]
            rng.shuffle(shuffled)
            got = stratified_split(shuffled, key, RATIOS, seed=7)
            self.assertEqual(base, got)

    def test_different_seed_different_split(self):
        items = make_skewed()
        key = lambda x: x.split("-")[0]
        s1 = stratified_split(items, key, RATIOS, seed=1)
        s2 = stratified_split(items, key, RATIOS, seed=2)
        self.assertNotEqual(s1["train"], s2["train"])

    def test_no_overlap_and_no_loss(self):
        items = make_skewed()
        splits = stratified_split(items, lambda x: x.split("-")[0],
                                  RATIOS, seed=7)
        train, val = splits["train"], splits["val"]
        self.assertEqual(len(train) + len(val), len(items))
        self.assertEqual(sorted(train + val), sorted(items))
        self.assertFalse(set(train) & set(val))


class TestRatioCloseness(unittest.TestCase):
    def test_deviation_small_on_skewed(self):
        items = make_skewed()
        key = lambda x: x.split("-")[0]
        splits = stratified_split(items, key, RATIOS, seed=7)
        report = distribution_report(items, key, RATIOS, splits)
        # 每层每侧偏差不超过一个样本对应的份额。
        for entry in report["per_class"]:
            n = entry["total"]
            for name in report["splits"]:
                self.assertLessEqual(
                    abs(entry["splits"][name]["deviation"]), 1.0 / n + 1e-9)
        # 整体偏差也非常小。
        for name in report["splits"]:
            self.assertLess(abs(report["overall"][name]["deviation"]), 0.01)

    def test_report_counts_consistent(self):
        items = make_skewed()
        key = lambda x: x.split("-")[0]
        splits = stratified_split(items, key, RATIOS, seed=7)
        report = distribution_report(items, key, RATIOS, splits)
        for entry in report["per_class"]:
            self.assertEqual(
                sum(entry["splits"][n]["count"] for n in report["splits"]),
                entry["total"])


class TestRareClassPolicy(unittest.TestCase):
    def test_min_per_split_guaranteed(self):
        # 2 个样本的稀有类，80/20 切分：纯随机可能全进 train，
        # 本策略保证 train/val 各 1 个。
        items = ["rare-0", "rare-1"] + ["common-%d" % i for i in range(100)]
        key = lambda x: "rare" if x.startswith("rare") else "common"
        splits = stratified_split(items, key, RATIOS, seed=0, min_per_split=1)
        self.assertEqual(sum(1 for x in splits["train"] if x.startswith("rare")), 1)
        self.assertEqual(sum(1 for x in splits["val"] if x.startswith("rare")), 1)

    def test_allocate_class_round_robin(self):
        # n < k * min_per_split 时轮询分配。
        self.assertEqual(_allocate_class(2, [0.8, 0.2], 1), [1, 1])
        self.assertEqual(_allocate_class(1, [0.8, 0.2], 1), [1, 0])
        self.assertEqual(_allocate_class(5, [0.5, 0.3, 0.2], 2), [2, 2, 1])
        self.assertEqual(_allocate_class(10, [0.8, 0.2], 1), [8, 2])
        self.assertEqual(_allocate_class(0, [0.8, 0.2], 1), [0, 0])


class TestEdgeCases(unittest.TestCase):
    def test_single_class(self):
        items = ["x-%d" % i for i in range(50)]
        splits = stratified_split(items, lambda x: "only", RATIOS, seed=3)
        self.assertEqual(len(splits["train"]), 40)
        self.assertEqual(len(splits["val"]), 10)

    def test_more_classes_than_samples(self):
        # 5 个类别、3 个样本：部分类别无样本，不影响切分。
        items = [("c0", 1), ("c1", 2), ("c2", 3)]
        splits = stratified_split(items, lambda x: x[0], RATIOS, seed=0)
        total = sum(len(v) for v in splits.values())
        self.assertEqual(total, 3)

    def test_one_sample_per_class(self):
        # 每类只有 1 个样本：无法按比例覆盖两侧，轮询起点按类别
        # 旋转，样本分散到两个子集而不是全落进一侧（纯随机切分的
        # 常见失败模式）。具体计数由种子决定，锁定当前行为。
        items = [("cls%d" % i, i) for i in range(10)]
        splits = stratified_split(items, lambda x: x[0], RATIOS, seed=0)
        self.assertEqual(len(splits["train"]) + len(splits["val"]), 10)
        self.assertEqual((len(splits["train"]), len(splits["val"])), (4, 6))
        # 三分切分：轮询起点按类别旋转，10 个类分散到三个子集，
        # 且结果确定（具体计数由种子决定，此处锁定当前行为）。
        splits3 = stratified_split(
            items, lambda x: x[0],
            {"a": 0.5, "b": 0.3, "c": 0.2}, seed=0)
        counts3 = tuple(len(splits3[n]) for n in ("a", "b", "c"))
        self.assertEqual(sum(counts3), 10)
        self.assertTrue(all(c >= 1 for c in counts3))
        self.assertEqual(counts3, (1, 4, 5))

    def test_duplicate_samples(self):
        # 含重复样本：多重集语义，切分后并集与输入一致。
        items = ["a", "a", "a", "b", "b", "c"] * 10
        splits = stratified_split(items, lambda x: x, RATIOS, seed=5)
        merged = sorted(splits["train"] + splits["val"])
        self.assertEqual(merged, sorted(items))
        # a/b/c 各有 30/20/10 个 -> 24/16/8 进 train，6/4/2 进 val。
        for cls, tr, va in (("a", 24, 6), ("b", 16, 4), ("c", 8, 2)):
            self.assertEqual(splits["train"].count(cls), tr)
            self.assertEqual(splits["val"].count(cls), va)
        # 重复样本下输入顺序仍无关。
        shuffled = items[:]
        random.Random(9).shuffle(shuffled)
        splits2 = stratified_split(shuffled, lambda x: x, RATIOS, seed=5)
        self.assertEqual(splits, splits2)

    def test_empty_input(self):
        splits = stratified_split([], lambda x: x, RATIOS, seed=0)
        self.assertEqual(splits, {"train": [], "val": []})

    def test_invalid_ratios(self):
        with self.assertRaises(ValueError):
            stratified_split([1], lambda x: x, {"a": 0.5, "b": 0.4})
        with self.assertRaises(ValueError):
            stratified_split([1], lambda x: x, {"a": 1.0, "b": 0.0})
        with self.assertRaises(ValueError):
            stratified_split([1], lambda x: x, {})


if __name__ == "__main__":
    unittest.main()
