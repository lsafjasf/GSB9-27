"""朴素贝叶斯分类器自测：对拍 + 边界用例 + 可复现性。纯标准库。

运行: python3 test_naive_bayes.py
"""

import json
import math
import os
import random

from naive_bayes import MultinomialNaiveBayes

HERE = os.path.dirname(os.path.abspath(__file__))
TOL = 1e-12

failures = []


def check(cond, msg):
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        failures.append(msg)


def load_golden():
    with open(os.path.join(HERE, "golden_cases.json"), encoding="utf-8") as f:
        return json.load(f)["cases"]


def test_golden():
    print("[1] 对拍 golden_cases.json（手算期望值）")
    for case in load_golden():
        X = [item["doc"] for item in case["train"]]
        y = [item["label"] for item in case["train"]]
        clf = MultinomialNaiveBayes(alpha=case["alpha"],
                                    class_prior=case["class_prior"]).fit(X, y)
        label, scores = clf.predict(case["doc"])
        check(label == case["expected"]["label"],
              f"{case['name']}: 预测类别 {label} == {case['expected']['label']}")
        for cls, (num, den) in case["expected"]["scores_frac"].items():
            expected = math.log(num / den)
            check(abs(scores[cls] - expected) < TOL,
                  f"{case['name']}: {cls} 对数分 {scores[cls]:.6f} == log({num}/{den})={expected:.6f}")


def test_unseen_alpha0():
    print("[2] alpha=0：未见特征给 -inf，已见特征仍正常")
    clf = MultinomialNaiveBayes(alpha=0.0).fit(
        [["a", "b"], ["b", "c"]], ["x", "y"])
    _, scores = clf.predict(["a"])
    check(math.isfinite(scores["x"]), "alpha=0 已见特征分数有限")
    _, scores_unseen = clf.predict(["zzz"])
    check(all(math.isinf(v) and v == float("-inf") for v in scores_unseen.values()),
          "alpha=0 未见特征 -> -inf")
    label, _ = clf.predict(["b"])
    check(label == "x", "alpha=0 已见 token 正常分类 (b 两类同频, 字典序 x)")


def test_single_class():
    print("[3] 单类别训练集")
    clf = MultinomialNaiveBayes().fit([["a", "b"], ["b", "c"]], ["c1", "c1"])
    label, scores = clf.predict(["zzz"])
    check(label == "c1" and len(scores) == 1, "只有一个类别时必然预测该类别")
    check(math.isfinite(scores["c1"]), "单类别 + 未见特征分数有限（平滑生效）")


def test_zero_features():
    print("[4] 特征数为零（训练集全是空文档）")
    clf = MultinomialNaiveBayes(class_prior="empirical").fit(
        [[], [], []], ["x", "x", "y"])
    check(len(clf.vocabulary) == 0, "词表大小为 0")
    label, scores = clf.predict([])
    check(label == "x", "退化为按先验分类 -> 多数类 x")
    check(abs(scores["x"] - math.log(2 / 3)) < TOL and abs(scores["y"] - math.log(1 / 3)) < TOL,
          "零特征时分数恰好等于 log 先验")
    label2, scores2 = clf.predict(["unknown"])
    check(label2 == "x" and scores2 == scores,
          "零特征模型遇到任意 token 不产生信息，分数与空文档一致")


def test_reproducibility():
    print("[5] 可复现性：乱序重复训练参数与预测完全一致")
    base_X = [["buy", "cheap"], ["cheap", "money"], ["meeting", "team"],
              ["team", "schedule"], ["cheap", "deal"]]
    base_y = ["spam", "spam", "ham", "ham", "spam"]
    ref = MultinomialNaiveBayes(alpha=0.7, class_prior="empirical").fit(base_X, base_y)
    rng = random.Random(1234)
    for trial in range(5):
        order = list(range(len(base_X)))
        rng.shuffle(order)
        X = [base_X[i] for i in order]
        y = [base_y[i] for i in order]
        clf = MultinomialNaiveBayes(alpha=0.7, class_prior="empirical").fit(X, y)
        check(clf.vocabulary == ref.vocabulary and clf.classes == ref.classes,
              f"trial {trial}: 词表与类别序列一致（与输入顺序无关）")
        check(all(abs(clf.log_prior[c] - ref.log_prior[c]) == 0.0 for c in ref.classes),
              f"trial {trial}: 对数先验逐位一致")
        check(
            all(abs(clf.log_feature_prob[c][t] - ref.log_feature_prob[c][t]) == 0.0
                for c in ref.classes for t in ref.vocabulary),
            f"trial {trial}: 对数特征概率逐位一致")
        for doc in (["cheap", "team"], ["nope"], []):
            l1, s1 = ref.predict(doc)
            l2, s2 = clf.predict(doc)
            check(l1 == l2 and s1 == s2, f"trial {trial}: 预测 {doc} 完全一致")


def test_prior_modes_and_validation():
    print("[6] 先验模式与参数校验")
    X, y = [["w"], ["z"], ["z"], ["z"]], ["pos", "neg", "neg", "neg"]
    empirical = MultinomialNaiveBayes(class_prior="empirical").fit(X, y)
    check(abs(empirical.log_prior["neg"] - math.log(0.75)) < TOL,
          "empirical: 先验=类别频率 3/4")
    uniform = MultinomialNaiveBayes(class_prior="uniform").fit(X, y)
    check(abs(uniform.log_prior["neg"] - math.log(0.5)) < TOL,
          "uniform: 先验=1/K，忽略不平衡")
    custom = MultinomialNaiveBayes(class_prior={"pos": 0.2, "neg": 0.8}).fit(X, y)
    check(abs(custom.log_prior["neg"] - math.log(0.8)) < TOL, "自定义先验生效")
    for kwargs, err in (
        ({"alpha": -1}, "alpha<0 报错"),
        ({"class_prior": {"pos": 0.5}}, "自定义先验缺类别报错"),
    ):
        try:
            MultinomialNaiveBayes(**kwargs).fit(X, y)
            check(False, err)
        except ValueError:
            check(True, err)
    try:
        MultinomialNaiveBayes().fit(X, y[:-1])
        check(False, "X/y 长度不一致报错")
    except ValueError:
        check(True, "X/y 长度不一致报错")


def test_predict_before_fit():
    print("[7] 未训练即预测报错")
    try:
        MultinomialNaiveBayes().predict(["a"])
        check(False, "未训练预测抛 RuntimeError")
    except RuntimeError:
        check(True, "未训练预测抛 RuntimeError")


if __name__ == "__main__":
    test_golden()
    test_unseen_alpha0()
    test_single_class()
    test_zero_features()
    test_reproducibility()
    test_prior_modes_and_validation()
    test_predict_before_fit()
    print()
    if failures:
        print(f"共 {len(failures)} 项失败")
        raise SystemExit(1)
    print("全部通过")
