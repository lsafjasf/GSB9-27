"""位掩码精确求解器：多维 0/1 背包（带权值最大化 + 可行性约束）。

只使用 Python 3 标准库。

问题定义
--------
给定 n 个元素，元素 i 有价值 v[i]（非负整数）和 m 维消耗 w[i][d]
（非负整数），每一维有容量 C[d]。要求选出子集 S 使：

    对所有 d:  sum_{i in S} w[i][d] <= C[d]        （可行性约束）
    最大化     sum_{i in S} v[i]                   （带权值目标）

状态定义
--------
用一个 n 位整数 mask 表示子集；位 i = 1 表示选中元素 i。
对全部 2^n 个子集显式枚举：

    cost[mask][d] = sum_{i in mask} w[i][d]        （m 个 int64 数组）
    value[mask]   = sum_{i in mask} v[i]           （1 个 int64 数组）

状态转移（去掉 mask 的最低位所对应的元素 p）
--------------------------------------------
    p = mask & -mask 的位序号
    prev = mask ^ (1 << p)
    cost[mask][d] = cost[prev][d] + w[p][d]
    value[mask]   = value[prev]   + v[p]
    feasible(mask) = 对所有 d: cost[mask][d] <= C[d]

按 mask 数值升序计算时 prev < mask，故前驱一定已算出。
在所有可行子集中取 value 最大者；同值时取 mask 最小者
（确定性、可复现的“一个最优解”，与穷举对拍保持同一规则）。

复杂度：时间 O(2^n * m)，主存 O(2^n * m * 8 字节)（另有一个 8 字节的
value 数组，即总计 O(2^n * (m+1) * 8 字节)）。n 每加 1，状态数翻倍，
这是指数增长；因此必须在分配前给出内存上界并在超预算时直接拒绝。
"""

from __future__ import annotations

import sys
from array import array
from dataclasses import dataclass

DEFAULT_MEM_BUDGET_BYTES = 256 * 1024 * 1024  # 默认 256 MiB
HARD_N_LIMIT = 63                             # 1 << 63 已是 Python 大整数边界
_INT64_MAX = (1 << 63) - 1


class KnapsackError(ValueError):
    """输入非法。"""


class InfeasibleError(Exception):
    """没有任何可行子集（包括空集都不可行，即某维容量为负）。"""


class MemoryLimitExceeded(MemoryError):
    """估算所需内存超出预算，在分配任何大数组之前拒绝。"""


@dataclass(frozen=True)
class Solution:
    """求解结果。

    value       最优值；无解（infeasible）时为 -1
    mask        一个最优子集的位掩码（同值取最小 mask）；无解时为 0
    selected    被选元素下标列表（按升序）
    feasible    是否存在可行解
    """

    value: int
    mask: int
    selected: tuple[int, ...]
    feasible: bool


def state_count(n: int) -> int:
    """元素数为 n 时的状态数（上界且此处即精确值）：2^n。"""
    if not isinstance(n, int) or n < 0:
        raise KnapsackError("n 必须是非负整数")
    return 1 << n


def memory_bound_bytes(n: int, m: int = 1) -> int:
    """主存上界（字节）：(m+1) 个 int64 数组，各 2^n 项。

    这是上界：算法不做任何稀疏剪枝，全部 2^n 个状态都会被写入。
    Python 解释器等常数级开销另计，相对指数项可忽略。
    """
    if not isinstance(m, int) or m < 1:
        raise KnapsackError("m（约束维数）必须是 >=1 的整数")
    return state_count(n) * (m + 1) * 8


def _normalize(items, capacities):
    """把 items / capacities 规整为 (n, m, weights, values, caps)。

    items 支持两种写法：
      * [(value, weight), ...]                单维
      * [(value, [w_d0, w_d1, ...]), ...]     多维
    capacities：单维传 int，多维传 list[int]。
    """
    if not isinstance(items, (list, tuple)):
        raise KnapsackError("items 必须是列表，元素形如 (value, weight(s))")
    if not isinstance(capacities, (list, tuple)):
        capacities = [capacities]
    caps = list(capacities)
    m = len(caps)
    if m < 1:
        raise KnapsackError("至少需要一维容量")

    n = len(items)
    weights: list[tuple[int, ...]] = []
    values: list[int] = []
    for idx, item in enumerate(items):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise KnapsackError(f"items[{idx}] 必须是 (value, weight) 或 (value, [weights])")
        value, weight = item
        if not isinstance(value, int) or value < 0:
            raise KnapsackError(f"items[{idx}] 的价值必须是非负整数")
        if not isinstance(weight, (list, tuple)):
            weight = [weight]
        if len(weight) != m:
            raise KnapsackError(
                f"items[{idx}] 的消耗维数 {len(weight)} 与容量维数 {m} 不一致"
            )
        if any((not isinstance(x, int)) for x in weight):
            raise KnapsackError(f"items[{idx}] 的消耗必须是整数")
        if any(x < 0 for x in weight):
            raise KnapsackError(
                f"items[{idx}] 的消耗为负；本算法要求消耗非负（子集单调增长）"
            )
        if not all(isinstance(c, int) for c in caps):
            raise KnapsackError("容量必须是整数")
        weights.append(tuple(weight))
        values.append(value)
    return n, m, weights, values, caps


def solve(items, capacities, mem_budget_bytes: int = DEFAULT_MEM_BUDGET_BYTES):
    """用位掩码 DP 求一个最优可行子集。

    越界/超预算时抛出：KnapsackError、MemoryLimitExceeded、InfeasibleError。
    """
    n, m, weights, values, caps = _normalize(items, capacities)

    if n > HARD_N_LIMIT:
        raise MemoryLimitExceeded(
            f"n={n} 超过硬性上界 {HARD_N_LIMIT}（状态数 2^{n} 无法在可接受时间内处理）"
        )

    needed = memory_bound_bytes(n, m)
    if needed > mem_budget_bytes:
        raise MemoryLimitExceeded(
            f"n={n}, m={m}: 需要 {needed} 字节（{needed / 2**20:.1f} MiB）"
            f" 超过预算 {mem_budget_bytes} 字节（{mem_budget_bytes / 2**20:.1f} MiB）。"
            f" 状态数 = 2^{n} = {state_count(n)}，每增加一个元素状态数翻倍，"
            "请调大 mem_budget_bytes（仅在机器内存确实足够时）或改用分支定界/整数规划。"
        )

    total_value = sum(values)
    if total_value > _INT64_MAX or any(
        sum(weights[i][d] for i in range(n)) > _INT64_MAX for d in range(m)
    ):
        raise KnapsackError("子集总和超出 int64 范围")

    # 空集都放不下 => 不存在任何可行子集（消耗非负，可行性随元素加入单调变差）
    if any(c < 0 for c in caps):
        raise InfeasibleError(f"存在负容量 {caps}，连空集合都不可行")

    size = 1 << n

    # 紧凑存储：m 维消耗 + 1 维价值，全部为有符号 int64。
    # array * int 直接按元素数扩容，避免 bytes(8*size) 产生同等大小的临时对象
    cost_tables = [array("q", [0]) * size for _ in range(m)]
    value_table = array("q", [0]) * size

    best_value = 0
    best_mask = 0  # mask=0 是第一个被检查的可行集，天然满足“同值取最小 mask”

    for mask in range(1, size):
        lowbit = mask & -mask
        p = lowbit.bit_length() - 1
        prev = mask ^ lowbit
        v = value_table[prev] + values[p]
        value_table[mask] = v

        feasible = True
        for d in range(m):
            c = cost_tables[d][prev] + weights[p][d]
            cost_tables[d][mask] = c
            if c > caps[d]:
                feasible = False
                # 其余维仍须写入（数组状态完整），但跳过提前判定即可

        if feasible and v > best_value:
            best_value = v
            best_mask = mask

    selected = tuple(i for i in range(n) if (best_mask >> i) & 1)
    return Solution(value=best_value, mask=best_mask, selected=selected, feasible=True)


# ---------------------------------------------------------------------------
# 简单演示：python3 bitmask_dp.py
# ---------------------------------------------------------------------------
def _demo() -> None:
    # (价值, 重量)；容量 10
    items = [(60, 2), (100, 3), (120, 5), (30, 4), (90, 6)]
    cap = 10
    sol = solve(items, cap)
    print("单维背包演示  items =", items, " capacity =", cap)
    print("  最优值 =", sol.value, " 最优子集下标 =", list(sol.selected),
          " mask =", bin(sol.mask))

    items2 = [(10, [2, 3]), (8, [3, 1]), (12, [4, 5]), (6, [1, 4])]
    caps2 = [6, 7]
    sol2 = solve(items2, caps2)
    print("二维约束演示  items =", items2, " capacities =", caps2)
    print("  最优值 =", sol2.value, " 最优子集下标 =", list(sol2.selected),
          " mask =", bin(sol2.mask))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(__doc__)
    else:
        _demo()
