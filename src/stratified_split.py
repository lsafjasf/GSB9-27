"""分层切分库（仅标准库）。

核心保证：
1. 按分层键（label）切分，每层训练/验证比例与目标接近；
2. 稀有类别有明确的最小样本策略（见 ``min_per_side`` 与 ``rare_policy``）；
3. 可复现：结果只依赖 (samples, labels, ratio, seed)，与输入顺序无关；
4. 提供各层在两侧的分布对比与偏差量化。
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Callable, Hashable, Iterable, List, Sequence, Tuple

__all__ = ["stratified_split", "distribution_report", "format_report"]


def _stable_key(sample: Any) -> str:
    """样本的稳定排序键。

    用 repr 做全序排序键：同一种子下与输入顺序无关；
    重复样本键相同，彼此等价，不影响输出多重集合。
    """
    try:
        return repr(sample)
    except Exception:  # pragma: no cover - 极端防御
        return f"<{type(sample).__name__}:{id(sample)}>"


def _class_rng(seed: Hashable, label: Hashable) -> random.Random:
    """每个类别独立的确定性随机源，避免类别间相互影响。"""
    return random.Random(f"stratified-split\x00{seed!r}\x00{label!r}")


def _allocate_counts(
    class_sizes: List[Tuple[Hashable, int]],
    ratio: float,
    min_per_side: int,
    rare_policy: str,
) -> dict:
    """计算每个类别应划入训练集的样本数。

    策略：
    - 常规类（n >= 2*min_per_side）：floor(n*ratio) 后钳制到
      [min_per_side, n - min_per_side]，保证两侧都至少有 min_per_side 个；
    - 稀有类（n < 2*min_per_side）：按 rare_policy 处理，
      "train" 表示整类进训练集（默认，保证训练侧见过所有类别）；
    - 全局修正：按小数余数从大到小（同余数按类别名稳定排序）调整，
      使训练集总量贴近 round(N*ratio)。
    """
    if rare_policy != "train":
        raise ValueError(f"unsupported rare_policy: {rare_policy!r}")

    alloc = {}
    eligible = []  # (label, n, frac)
    for label, n in class_sizes:
        if n == 0:
            alloc[label] = 0
            continue
        if n < 2 * min_per_side:
            alloc[label] = n  # 稀有类：整类进训练集
            continue
        ideal = n * ratio
        base = int(math.floor(ideal))
        base = max(min_per_side, min(n - min_per_side, base))
        alloc[label] = base
        eligible.append((label, n, ideal - math.floor(ideal)))

    total = sum(n for _, n in class_sizes)
    target_total = int(round(total * ratio))
    # 稀有类贡献固定，只在常规类之间调节
    delta = target_total - sum(alloc.values())

    if delta > 0:  # 需要往训练集加：最大余数法，每轮每类至多 +1
        order = sorted(eligible, key=lambda t: (-t[2], repr(t[0])))
        while delta > 0:
            progressed = False
            for label, n, _ in order:
                if delta == 0:
                    break
                if alloc[label] < n - min_per_side:
                    alloc[label] += 1
                    delta -= 1
                    progressed = True
            if not progressed:
                break
    elif delta < 0:  # 需要往验证集挪：优先小数余数小的类，每轮每类至多 -1
        order = sorted(eligible, key=lambda t: (t[2], repr(t[0])))
        while delta < 0:
            progressed = False
            for label, n, _ in order:
                if delta == 0:
                    break
                if alloc[label] > min_per_side:
                    alloc[label] -= 1
                    delta += 1
                    progressed = True
            if not progressed:
                break
    return alloc


def stratified_split(
    samples: Sequence[Any],
    labels: Sequence[Hashable] | Callable[[Any], Hashable],
    train_ratio: float = 0.8,
    seed: Hashable = 0,
    min_per_side: int = 1,
    rare_policy: str = "train",
) -> Tuple[List[Any], List[Any]]:
    """分层切分为 (train, val)。

    参数：
        samples: 样本序列。
        labels: 与 samples 等长的分层键序列，或从样本取键的函数。
        train_ratio: 目标训练集比例，开区间 (0, 1)。
        seed: 任意可哈希种子；相同 (samples 多重集合, labels, ratio, seed)
              必得相同结果，与输入顺序无关。
        min_per_side: 常规类每侧最少样本数；类别样本数 >= 2*min_per_side
                      时保证两侧均不少于该值。
        rare_policy: 稀有类（样本数 < 2*min_per_side）策略，目前支持
                     "train"：整类划入训练集。
    返回：
        (train_samples, val_samples)，各自内部按稳定键排序，顺序确定。
    """
    if not 0.0 < train_ratio < 1.0:
        raise ValueError("train_ratio must be in (0, 1)")
    if min_per_side < 0:
        raise ValueError("min_per_side must be >= 0")

    samples = list(samples)
    if callable(labels):
        keys = [labels(s) for s in samples]
    else:
        keys = list(labels)
        if len(keys) != len(samples):
            raise ValueError("labels length must match samples length")

    # 1) 稳定排序：消除输入顺序影响（重复样本键相同，彼此等价）。
    order = sorted(range(len(samples)), key=lambda i: (_stable_key(samples[i]), repr(keys[i])))

    # 2) 按类别分组，并在类内做确定性洗牌。
    groups: dict = defaultdict(list)
    for i in order:
        groups[keys[i]].append(samples[i])
    for label, members in groups.items():
        _class_rng(seed, label).shuffle(members)

    # 3) 计算每类训练集配额。
    class_sizes = sorted(((label, len(m)) for label, m in groups.items()), key=lambda t: repr(t[0]))
    alloc = _allocate_counts(class_sizes, train_ratio, min_per_side, rare_policy)

    # 4) 切分并稳定排序输出。
    train, val = [], []
    for label, members in groups.items():
        k = alloc[label]
        train.extend(members[:k])
        val.extend(members[k:])
    train.sort(key=_stable_key)
    val.sort(key=_stable_key)
    return train, val


def distribution_report(
    train: Sequence[Any],
    val: Sequence[Any],
    labels: Callable[[Any], Hashable] | None = None,
    train_ratio: float = 0.8,
) -> dict:
    """各层在两侧的分布对比与偏差量化。

    返回 {"per_class": {label: {...}}, "overall": {...}}，每行含：
    train_count / val_count / actual_train_ratio / target_ratio /
    abs_deviation（|实际-目标|）。
    """
    key_fn = labels if callable(labels) else (lambda s: s)

    def _counts(items: Iterable[Any]) -> dict:
        c: dict = defaultdict(int)
        for it in items:
            c[key_fn(it)] += 1
        return dict(c)

    ct, cv = _counts(train), _counts(val)
    per_class = {}
    for label in sorted(set(ct) | set(cv), key=repr):
        nt, nv = ct.get(label, 0), cv.get(label, 0)
        n = nt + nv
        actual = nt / n if n else 0.0
        per_class[label] = {
            "train_count": nt,
            "val_count": nv,
            "actual_train_ratio": actual,
            "target_ratio": train_ratio,
            "abs_deviation": abs(actual - train_ratio),
        }
    nt, nv = len(train), len(val)
    n = nt + nv
    overall = {
        "train_count": nt,
        "val_count": nv,
        "actual_train_ratio": nt / n if n else 0.0,
        "target_ratio": train_ratio,
        "abs_deviation": abs(nt / n - train_ratio) if n else 0.0,
    }
    return {"per_class": per_class, "overall": overall}


def format_report(report: dict) -> str:
    """把 distribution_report 的结果格式化为对齐文本表。"""
    header = f"{'class':<20} {'train':>6} {'val':>6} {'actual':>8} {'target':>8} {'|dev|':>8}"
    lines = [header, "-" * len(header)]
    for label, row in report["per_class"].items():
        lines.append(
            f"{repr(label):<20.20} {row['train_count']:>6} {row['val_count']:>6} "
            f"{row['actual_train_ratio']:>8.4f} {row['target_ratio']:>8.4f} "
            f"{row['abs_deviation']:>8.4f}"
        )
    o = report["overall"]
    lines += [
        "-" * len(header),
        f"{'OVERALL':<20} {o['train_count']:>6} {o['val_count']:>6} "
        f"{o['actual_train_ratio']:>8.4f} {o['target_ratio']:>8.4f} {o['abs_deviation']:>8.4f}",
    ]
    return "\n".join(lines)
