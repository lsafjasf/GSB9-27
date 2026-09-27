"""Type-2: 标识符与字面量全部重命名，逻辑完全一致。"""


def average_score(records):
    total = 0
    count = 0
    for rec in records:
        if rec["score"] is not None:
            total += rec["score"]
            count += 1
    if count == 0:
        return 0.0
    return total / count


def mean_rating(entries):
    acc = 0
    n = 0
    for item in entries:
        if item["rating"] is not None:
            acc += item["rating"]
            n += 1
    if n == 0:
        return 0.0
    return acc / n
