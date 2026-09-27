"""lexkth: 按字典序取第 K 个组合 / 排列（支持重复元素，纯标准库）。

不枚举全部组合，而是逐位（从最高位到最低位）用计数函数确定每一位的取值：
对当前位尝试每个候选值 v（升序），计算"前缀固定后、以 v 开头"的合法
序列条数 block；若 k > block 则 k -= block 并尝试下一个候选值，否则当前位
确定为 v，进入下一位。这正是字典序排名（unranking）的标准做法。

计数函数推导
============

1) 多重集排列 kth_permutation
   元素为多重集，distinct 值为 v_1<...<v_m，重数为 c_1..c_m，n = sum(c_i)。
   总排列数（多项式系数）：
       P = n! / (c_1! c_2! ... c_m!)
         = C(n, c_1) * C(n-c_1, c_2) * ... * C(c_m, c_m)
   固定首位为 v_i 后，剩余多重集的重数为 c_i-1，其余不变，故
       block_i = (n-1)! / (c_1! ... (c_i-1)! ... c_m!)
   用同样的多项式系数公式计算即可。

2) 可重复组合 kth_combination(..., allow_repetition=True)
   从 m 种 distinct 值中可重复地取 r 个（每种不限次数），序列按非降序表示。
   由 stars-and-bars，总数为 C(m+r-1, r)。
   固定当前位为第 j 种值后，剩余 r-1 个位置只能从第 j..m 种值中取（保持
   非降序），故
       block_j = C((m-j+1) + (r-1) - 1, r-1) = C(m-j+r-2, r-1)

3) 不可重复组合 kth_combination(..., allow_repetition=False)
   每种值 v_i 最多使用其在输入中的重数 c_i 次，取 r 个。
   总数是生成函数 prod_{i}(1 + x + ... + x^{c_i}) 中 x^r 的系数，用背包式
   DP 计算：dp[s] = 处理完前 i 种值后凑出 s 个元素的方案数，转移
       dp'[s] = sum_{t=0..min(c_i, s)} dp[s-t]
   固定当前位为第 j 种值后，c_j 减 1，剩余 r-1 个位置仍可从第 j..m 种值
   中取（保持非降序），block 即对该后缀多重集再跑一次同一 DP。
   当所有 c_i = 1 时退化为经典的 C(n, r)。

数值范围：Python 整数任意精度，计数接近甚至超过 2^63 也不会溢出。
"""

from math import comb

__all__ = ["kth_permutation", "kth_combination", "count_permutations",
           "count_combinations"]


def _multinomial(counts):
    """多项式系数 (sum counts)! / prod(c_i!)，用二项式系数连乘计算。"""
    total = 0
    result = 1
    for c in counts:
        total += c
        result *= comb(total, c)
    return result


def _bounded_combos(counts, r):
    """每种类型 i 最多取 counts[i] 个、共取 r 个的方案数（生成函数系数）。"""
    if r < 0:
        return 0
    dp = [0] * (r + 1)
    dp[0] = 1
    for c in counts:
        if c <= 0:
            continue
        ndp = [0] * (r + 1)
        for s in range(r + 1):
            ndp[s] = sum(dp[s - t] for t in range(min(c, s) + 1))
        dp = ndp
    return dp[r]


def _distinct_counts(items):
    """返回 (升序 distinct 值列表, 对应重数列表)。"""
    counts = {}
    for x in items:
        counts[x] = counts.get(x, 0) + 1
    values = sorted(counts)
    return values, [counts[v] for v in values]


def _check_k(k, total, what):
    if not isinstance(k, int) or isinstance(k, bool):
        raise TypeError(f"k 必须是整数，得到 {type(k).__name__}")
    if not 1 <= k <= total:
        if total > 0:
            raise ValueError(
                f"k={k} 越界：{what}共 {total} 个，合法范围是 1..{total}")
        raise ValueError(f"k={k} 越界：{what}为空（0 个），不存在合法的 k")


def count_permutations(items):
    """多重集 items 的 distinct 排列总数。"""
    _, counts = _distinct_counts(items)
    return _multinomial(counts)


def kth_permutation(items, k):
    """多重集 items 的全部 distinct 排列按字典序排列后，返回第 k 个（1 起始）。

    返回与 items 元素类型一致的 list。k 越界时抛出 ValueError 并给出合法范围。
    """
    values, counts = _distinct_counts(items)
    total = _multinomial(counts)
    _check_k(k, total, "排列空间")
    k -= 1  # 转为 0 起始

    result = []
    remaining = sum(counts)
    for _ in range(remaining):
        for i, v in enumerate(values):
            if counts[i] == 0:
                continue
            counts[i] -= 1
            block = _multinomial(counts)
            if k < block:
                result.append(v)
                break
            k -= block
            counts[i] += 1
    return result


def count_combinations(items, r, allow_repetition=False):
    """从多重集 items 中取 r 个元素的组合总数。

    allow_repetition=False: 每种值最多用其在 items 中的重数次；
    allow_repetition=True:  每种 distinct 值不限次数（经典可重复组合）。
    """
    if r < 0:
        raise ValueError(f"r 必须非负，得到 r={r}")
    values, counts = _distinct_counts(items)
    if allow_repetition:
        m = len(values)
        return comb(m + r - 1, r) if m > 0 else (1 if r == 0 else 0)
    return _bounded_combos(counts, r)


def kth_combination(items, k, r, allow_repetition=False):
    """从多重集 items 中取 r 个的组合（非降序序列）按字典序的第 k 个（1 起始）。

    allow_repetition 区分两类问题：
      False —— 不可重复：每种值最多用其在 items 中出现的次数；
      True  —— 可重复：  每种 distinct 值可无限次使用。
    返回长度为 r 的 list。k 越界时抛出 ValueError 并给出合法范围。
    """
    if r < 0:
        raise ValueError(f"r 必须非负，得到 r={r}")
    values, counts = _distinct_counts(items)
    m = len(values)
    total = count_combinations(items, r, allow_repetition)
    _check_k(k, total,
             f"组合空间({'可重复' if allow_repetition else '不可重复'}, r={r})")
    k -= 1  # 转为 0 起始

    result = []
    lo = 0  # 非降序约束：下一位只能取 values[lo:]
    for pos in range(r):
        slots = r - pos - 1  # 当前位确定后还剩的位置数
        for j in range(lo, m):
            if not allow_repetition and counts[j] == 0:
                continue
            if allow_repetition:
                # 固定当前位为 values[j]，剩余 slots 个从第 j..m 种值中可重复取
                block = comb(m - j + slots - 1, slots) if slots > 0 else 1
            else:
                counts[j] -= 1
                block = _bounded_combos(counts[j:], slots)
            if k < block:
                result.append(values[j])
                lo = j
                break
            k -= block
            if not allow_repetition:
                counts[j] += 1
    return result
