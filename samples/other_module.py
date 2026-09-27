"""跨文件克隆: 与 renamed_clone.py 中的 average_score 是重命名克隆。"""


def calc_mean_value(items):
    s = 0
    c = 0
    for it in items:
        if it["score"] is not None:
            s += it["score"]
            c += 1
    if c == 0:
        return 0.0
    return s / c
