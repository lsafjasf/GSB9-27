"""分层切分库（仅标准库）。

按分层键把样本切分为多个子集（如 train/val），保证：
1. 每层（类别）在各子集中的比例与目标比例尽量接近；
2. 稀有类别有明确的最小样本策略（见 MIN_PER_SPLIT 策略说明）；
3. 结果可复现：由种子驱动的稳定排序，与输入顺序无关，多次运行完全一致；
4. 提供分布对比报告，量化实际比例与目标的偏差。

稀有类别最小样本策略
--------------------
设类别样本数为 n，子集数为 k，每子集最小样本数为 m（默认 1）：
- n >= k * m：先对 n 整体按目标比例做最大余数法分配，再把低于
  保底 m 的子集从盈余最多的子集逐一补齐，保证每个子集至少 m 个，
  且与目标比例的偏差不超过一个样本的份额。
- n < k * m：按稳定排序轮询（round-robin）分配，每个子集得到
  floor(n/k) 或 ceil(n/k) 个；轮询起点按类别由种子确定性地旋转，
  避免所有稀有类的样本都落入同一个子集。此时某些子集可能为 0，
  属于不可避免的物理限制，报告中会如实反映。
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict


def _stable_digest(item, seed):
    """由 (seed, repr(item)) 决定的稳定摘要，与输入顺序、进程无关。"""
    payload = "{}|{!r}".format(seed, item).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _largest_remainder(n, ratios):
    """把 n 按 ratios（和为 1 的正数序列）拆成整数份，使用最大余数法。"""
    k = len(ratios)
    raw = [n * r for r in ratios]
    base = [int(math.floor(x)) for x in raw]
    rest = n - sum(base)
    # 余数从大到小依次 +1；余数相同按下标小者优先，保证确定性。
    order = sorted(range(k), key=lambda i: (-(raw[i] - base[i]), i))
    for i in order[:rest]:
        base[i] += 1
    return base


def _allocate_class(n, ratios, min_per_split, rotate=0):
    """返回长度为 k 的整数列表，表示该类别在每个子集中的样本数。

    rotate: 轮询分配时的起点偏移（按类别确定性地给出），仅用于
    n < k * min_per_split 的稀有类情形。
    """
    k = len(ratios)
    if n <= 0:
        return [0] * k
    if n < k * min_per_split:
        # 稀有类别：轮询分配，起点按 rotate 旋转（确定性）。
        counts = [n // k] * k
        for j in range(n % k):
            counts[(rotate + j) % k] += 1
        return counts
    # 先对整体用最大余数法，再把低于保底的子集从盈余最多的子集补齐。
    counts = _largest_remainder(n, ratios)
    for i in range(k):
        while counts[i] < min_per_split:
            # 从 (数量 - 保底) 盈余最多的子集挪一个；并列取下标小者，保证确定。
            donor = max(range(k), key=lambda j: (counts[j] - min_per_split, -j))
            if counts[donor] - min_per_split <= 0:
                break  # 理论上 n >= k*m 时不会发生
            counts[donor] -= 1
            counts[i] += 1
    return counts


def stratified_split(items, key_fn, ratios, seed=0, min_per_split=1):
    """按 key_fn 分层切分 items。

    参数:
        items: 可迭代样本集（允许重复样本）。
        key_fn: 样本 -> 分层键（类别标签）。
        ratios: dict，子集名 -> 目标比例，比例之和必须为 1，如
                {"train": 0.8, "val": 0.2}。子集顺序按插入顺序固定。
        seed: 整数种子；相同 (items 多重集, key_fn, ratios, seed) 必得
              相同结果，与 items 的输入顺序无关。
        min_per_split: 每个子集在每层的最小样本数（受类别大小限制）。

    返回:
        dict，子集名 -> 样本列表。列表内部按稳定摘要排序，完全确定。
    """
    names = list(ratios.keys())
    if not names:
        raise ValueError("ratios 不能为空")
    values = [float(ratios[n]) for n in names]
    if any(v <= 0 for v in values):
        raise ValueError("所有比例必须为正数")
    total = sum(values)
    if abs(total - 1.0) > 1e-9:
        raise ValueError("比例之和必须为 1，当前为 {!r}".format(total))
    if min_per_split < 0:
        raise ValueError("min_per_split 不能为负")

    groups = defaultdict(list)
    for item in items:
        groups[key_fn(item)].append(item)

    result = {name: [] for name in names}
    # 类别按键排序遍历，保证确定性（与 dict 插入顺序无关）。
    for cls in sorted(groups, key=repr):
        members = groups[cls]
        # 稳定排序：摘要有 seed 参与，相同输入多重集排序结果一致。
        members.sort(key=lambda it: _stable_digest(it, seed))
        rotate = int(_stable_digest(cls, seed), 16) % len(names)
        counts = _allocate_class(len(members), values, min_per_split, rotate)
        pos = 0
        for name, cnt in zip(names, counts):
            result[name].extend(members[pos:pos + cnt])
            pos += cnt

    # 输出顺序也固定，便于逐元素比较。
    for name in names:
        result[name].sort(key=lambda it: _stable_digest(it, seed))
    return result


def distribution_report(items, key_fn, ratios, splits):
    """生成分布对比数据：每层在各子集的实际比例 vs 目标比例及偏差。

    返回 dict:
        {
          "splits": [子集名...],
          "per_class": [
            {"class": 类别, "total": n,
             "splits": {子集名: {"count": c, "actual": 实际比例,
                                 "target": 目标比例, "deviation": 偏差}}},
            ...
          ],
          "overall": {子集名: {"count": c, "actual": a,
                               "target": t, "deviation": d}},
          "max_abs_deviation": 所有层/子集偏差的最大绝对值,
        }
    """
    names = list(ratios.keys())
    total_by_class = defaultdict(int)
    for item in items:
        total_by_class[key_fn(item)] += 1

    count_by = {cls: {name: 0 for name in names} for cls in total_by_class}
    for name in names:
        for item in splits[name]:
            count_by[key_fn(item)][name] += 1

    per_class = []
    max_dev = 0.0
    for cls in sorted(total_by_class, key=repr):
        n = total_by_class[cls]
        entry = {"class": cls, "total": n, "splits": {}}
        for name in names:
            c = count_by[cls][name]
            actual = c / n if n else 0.0
            target = float(ratios[name])
            dev = actual - target
            max_dev = max(max_dev, abs(dev))
            entry["splits"][name] = {
                "count": c, "actual": actual,
                "target": target, "deviation": dev,
            }
        per_class.append(entry)

    grand = sum(total_by_class.values())
    overall = {}
    for name in names:
        c = sum(count_by[cls][name] for cls in total_by_class)
        actual = c / grand if grand else 0.0
        target = float(ratios[name])
        overall[name] = {
            "count": c, "actual": actual,
            "target": target, "deviation": actual - target,
        }

    return {
        "splits": names,
        "per_class": per_class,
        "overall": overall,
        "max_abs_deviation": max_dev,
    }


def format_report(report):
    """把 distribution_report 的结果格式化为可读文本。"""
    names = report["splits"]
    lines = []
    header = "{:<12} {:>8}".format("class", "total")
    for name in names:
        header += "  {:>22}".format(name + " cnt/act/tgt/dev")
    lines.append(header)
    lines.append("-" * len(header))
    for entry in report["per_class"]:
        row = "{!r:<12} {:>8}".format(str(entry["class"]), entry["total"])
        for name in names:
            s = entry["splits"][name]
            row += "  {:>5} {:>5.3f} {:>5.3f} {:>+6.3f}".format(
                s["count"], s["actual"], s["target"], s["deviation"])
        lines.append(row)
    lines.append("-" * len(header))
    row = "{:<12} {:>8}".format("OVERALL", sum(e["total"] for e in report["per_class"]))
    for name in names:
        s = report["overall"][name]
        row += "  {:>5} {:>5.3f} {:>5.3f} {:>+6.3f}".format(
            s["count"], s["actual"], s["target"], s["deviation"])
    lines.append(row)
    lines.append("max_abs_deviation = {:.6f}".format(report["max_abs_deviation"]))
    return "\n".join(lines)
