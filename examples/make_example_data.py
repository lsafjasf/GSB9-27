"""生成示例数据：reference.jsonl（基线参考）与 current.jsonl（待检数据）。

待检数据故意混入：缺失、重复 id、超范围年龄、类型错误、渠道分布漂移。
运行：python examples/make_example_data.py
"""
import json
import random

rng = random.Random(20260928)
CHANNELS_REF = ["app"] * 50 + ["web"] * 30 + ["api"] * 15 + ["batch"] * 5


def write(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# 参考数据：分布正常，用于冻结基线
ref = []
for i in range(20000):
    ref.append({
        "id": i,
        "age": max(0, min(120, int(rng.gauss(35, 12)))),
        "channel": rng.choice(CHANNELS_REF),
        "score": round(rng.uniform(0, 100), 2),
    })
write("examples/reference.jsonl", ref)

# 待检数据：混入质量问题 + 渠道分布漂移（app 50% -> 20%，api 15% -> 45%）
channels_new = ["app"] * 20 + ["web"] * 30 + ["api"] * 45 + ["batch"] * 5
cur = []
for i in range(5000):
    row = {
        "id": i,
        "age": max(0, min(120, int(rng.gauss(35, 12)))),
        "channel": rng.choice(channels_new),
        "score": round(rng.uniform(0, 100), 2),
    }
    if i % 997 == 0:
        row["age"] = None            # 缺失
    if i % 991 == 0:
        row["id"] = i - 1            # 重复
    if i % 983 == 0:
        row["age"] = 999             # 超范围
    if i % 977 == 0:
        row["score"] = "N/A"         # 类型错误
    cur.append(row)
write("examples/current.jsonl", cur)
print("已生成 examples/reference.jsonl (20000 行) 与 examples/current.jsonl (5000 行)")
