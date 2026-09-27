"""穷举搜索参考实现（与 bitmask_dp 完全独立编写）。

朴素枚举全部 2^n 个子集：直接对每个子集累加价值与各维消耗，
不做任何递推，用于和位掩码 DP 对拍。
比较规则与 DP 一致：value 最大，同值取最小 mask。
"""

from __future__ import annotations

from bitmask_dp import InfeasibleError, KnapsackError, Solution


def solve_bruteforce(items, capacities):
    if not isinstance(capacities, (list, tuple)):
        capacities = [capacities]
    caps = list(capacities)
    n = len(items)

    vals = []
    ws = []
    for idx, item in enumerate(items):
        value, weight = item
        if not isinstance(weight, (list, tuple)):
            weight = [weight]
        if len(weight) != len(caps):
            raise KnapsackError(f"items[{idx}] 维数不匹配")
        if any(x < 0 for x in weight) or value < 0:
            raise KnapsackError("穷举参考同样要求非负整数输入")
        vals.append(value)
        ws.append(weight)

    if any(c < 0 for c in caps):
        raise InfeasibleError("负容量：空集不可行")

    best_value = -1
    best_mask = 0
    found = False

    for mask in range(1 << n):
        total_v = 0
        totals = [0] * len(caps)
        for i in range(n):
            if (mask >> i) & 1:
                total_v += vals[i]
                for d in range(len(caps)):
                    totals[d] += ws[i][d]
        ok = all(totals[d] <= caps[d] for d in range(len(caps)))
        if ok:
            found = True
            # mask 升序，严格大于才更新 => 同值保留最小 mask
            if total_v > best_value:
                best_value = total_v
                best_mask = mask

    if not found:
        # 非负消耗下不会发生（空集可行），保留防御性分支
        return Solution(value=-1, mask=0, selected=(), feasible=False)

    selected = tuple(i for i in range(n) if (best_mask >> i) & 1)
    return Solution(value=best_value, mask=best_mask, selected=selected, feasible=True)
