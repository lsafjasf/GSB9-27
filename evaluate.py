#!/usr/bin/env python3
"""evaluate.py — 用人工标注的 labels.json 对检测结果对拍，输出准确率/误报率。"""
import json
import os
import sys

import clone_detector as cd

HERE = os.path.dirname(os.path.abspath(__file__))


def base_key(key):
    """'路径/文件.py::函数' -> '文件.py::函数'，与标注格式对齐。"""
    path, _, name = key.partition("::")
    return os.path.basename(path) + "::" + name


def pair_id(a, b):
    return tuple(sorted((a, b)))


def main():
    min_tokens = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 0.6

    with open(os.path.join(HERE, "labels.json"), encoding="utf-8") as fh:
        labeled = json.load(fh)["pairs"]

    blocks, pairs = cd.detect([os.path.join(HERE, "samples")],
                              min_tokens=min_tokens, threshold=threshold)
    detected = {}
    for p in pairs:
        detected[pair_id(base_key(p.a.key), base_key(p.b.key))] = p.similarity

    tp = fp = fn = tn = 0
    print("%-58s %-6s %-6s %s" % ("标注对", "标注", "检出", "相似度"))
    for item in labeled:
        lid = pair_id(item["a"], item["b"])
        got = lid in detected
        sim = detected.get(lid)
        if item["clone"] and got:
            tp += 1
        elif item["clone"] and not got:
            fn += 1
        elif not item["clone"] and got:
            fp += 1
        else:
            tn += 1
        mark = ""
        if (item["clone"] and not got) or (not item["clone"] and got):
            mark = "  <-- 不一致"
        print("%-58s %-6s %-6s %s%s" % (
            "%s  <->  %s" % (item["a"], item["b"]),
            "克隆" if item["clone"] else "非克隆",
            "是" if got else "否",
            "%.3f" % sim if sim is not None else "-",
            mark,
        ))

    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    accuracy = (tp + tn) / (tp + tn + fp + fn)
    print()
    print("TP=%d FP=%d FN=%d TN=%d" % (tp, fp, fn, tn))
    print("准确率 accuracy : %.3f" % accuracy)
    print("精确率 precision: %.3f" % precision)
    print("召回率 recall   : %.3f" % recall)
    print("误报率 FPR      : %.3f" % fpr)

    labeled_ids = {pair_id(i["a"], i["b"]) for i in labeled}
    extra = [k for k in detected if k not in labeled_ids]
    if extra:
        print()
        print("检出但未标注的对(需人工复核): %d" % len(extra))
        for k in sorted(extra):
            print("  %.3f  %s  <->  %s" % (detected[k], k[0], k[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
