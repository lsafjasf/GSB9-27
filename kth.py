"""按字典序取第 K 个「多集合」组合 / 排列。

只使用 Python 标准库；依赖 Python 3 的任意精度整数，组合空间可以远大于
64 位整数上限。

公开接口：
- kth_combination(elements, r, k): 第 k 个长度为 r 的多集合组合（每个值的
  可用副本数等于它在 elements 中出现的次数；等价于
  sorted(set(itertools.combinations(sorted(elements), r)))）。
- kth_permutation(elements, k):   第 k 个多集合全排列（等价于
  sorted(set(itertools.permutations(elements)))）。
- kth_multiset(elements, k, kind, r=None): 统一入口，用 kind 区分两类问题。
- multiset_combination_count / multiset_permutation_count: 计数函数。

约定：K 从 1 开始；字典序即 Python 元组的字典序；相同元素不可区分，因此
相同的值序列只算一个结果（多集合语义）。
"""

from collections import Counter
from math import factorial
from typing import Iterable, List, Optional, Sequence, Tuple, Union

Number = Union[int, float]


class KthError(ValueError):
    """K 越界或参数非法时抛出。"""


# ---------------------------------------------------------------------------
# 计数函数
# ---------------------------------------------------------------------------

def multiset_permutation_count(counts: Sequence[int]) -> int:
    """多集合全排列数：n! / (c_0! c_1! ... c_(m-1)!)。

    推导：先把 n 个位置上的元素当作互不相同，共 n! 种排法；第 i 种值内部
    的 c_i 个相同元素互换不会产生新序列，即被多算了 c_i! 倍，依次除回即可。
    """
    counts = list(counts)
    result = factorial(sum(counts))
    for c in counts:
        result //= factorial(c)
    return result


def multiset_combination_count(counts: Sequence[int], r: int) -> int:
    """从每种值有 counts[i] 个副本的多集合中选 r 个元素的组合数。

    动态规划：
        dp[t] = 只考虑已加入的若干种值时，恰好选 t 个的方案数。
    初始 dp[0] = 1（一种值都不考虑时只有选 0 个这一种方案）。
    每加入一种有 c 个副本的值，该值可取 0..c 个：
        new_dp[t] = dp[t] + dp[t-1] + ... + dp[t-c]
    用滑动窗口在 O(1) 内求出每一项，整体 O(m*r)。
    无界的「有重复组合 C(m+r-1, r)」是 c >= r 时的特例。
    """
    if r < 0:
        return 0
    dp: List[int] = [1]
    cap = 0
    for c in counts:
        cap = min(r, cap + c)
        nxt = [0] * (cap + 1)
        window = 0
        for t in range(cap + 1):
            window += dp[t] if t < len(dp) else 0
            if 0 <= t - c - 1 < len(dp):
                window -= dp[t - c - 1]
            nxt[t] = window
        dp = nxt
    return dp[r] if 0 <= r < len(dp) else 0


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _normalize(elements: Iterable, k: Number, r: Optional[int] = None) -> Tuple[list, list, int, Optional[int]]:
    try:
        values = sorted(elements)
    except TypeError as exc:
        raise KthError("elements 中的元素必须可相互比较") from exc
    if not isinstance(k, int) or isinstance(k, bool):
        raise KthError("k 必须是整数（从 1 开始）")
    if r is not None and (not isinstance(r, int) or isinstance(r, bool) or r < 0):
        raise KthError("r 必须是非负整数")
    if r is not None and r > len(values):
        raise KthError(f"r={r} 超过元素总数 n={len(values)}")
    grouped = [[value, cnt] for value, cnt in Counter(values).items()]
    return values, grouped, k, r


def _check_k(k: int, total: int, noun: str) -> None:
    if not (1 <= k <= total):
        raise KthError(f"k={k} 越界：合法范围为 1..{total}（共 {total} 个{noun}）")


def _suffix_tables(counts: Sequence[int], r: int) -> Tuple[List[List[int]], List[List[int]]]:
    """后缀计数表与前缀和。

    ways[i][t] = 只使用第 i..m-1 种值（各值使用完整 counts 个副本）选 t 个
    的方案数。递推（从后往前）：
        ways[i][t] = sum_{x=0..min(c_i, t)} ways[i+1][t-x]
    pref[i] 为 ways[i] 的前缀和，便于 O(1) 求区间和；ways[m] = [1]。
    """
    m = len(counts)
    ways: List[List[int]] = [[] for _ in range(m + 1)]
    pref: List[List[int]] = [[] for _ in range(m + 1)]
    ways[m] = [1]
    pref[m] = [0, 1]
    suffix_len = 0
    for i in range(m - 1, -1, -1):
        c = counts[i]
        cap = min(r, suffix_len + c)
        row = [0] * (cap + 1)
        nxt = ways[i + 1]
        window = 0
        for t in range(cap + 1):
            window += nxt[t] if t < len(nxt) else 0
            if 0 <= t - c - 1 < len(nxt):
                window -= nxt[t - c - 1]
            row[t] = window
        ways[i] = row
        pref_i = [0] * (len(row) + 1)
        running = 0
        for t, v in enumerate(row):
            running += v
            pref_i[t + 1] = running
        pref[i] = pref_i
        suffix_len = cap
    return ways, pref


def _range_sum(pref_row: Sequence[int], lo: int, hi: int) -> int:
    """pref_row 是某行 ways 的前缀和；求 ways[lo..hi]（越界项视为 0）。"""
    if hi < 0 or lo > hi:
        return 0
    lo = max(lo, 0)
    hi = min(hi, len(pref_row) - 2)
    if lo > hi:
        return 0
    return pref_row[hi + 1] - pref_row[lo]


# ---------------------------------------------------------------------------
# 逐位定位
# ---------------------------------------------------------------------------

def kth_permutation(elements: Iterable, k: int) -> Tuple:
    """按字典序返回多集合的第 k 个全排列。

    定位第 1 位时，若该位放值 v（当前有 c_v 个副本，剩余元素共 n 个，
    剩余排列总数为 T），则「第 1 位为 v」这一块的大小为
        (n-1)! / ((c_v-1)! * prod_{u!=v} c_u!) = T * c_v / n。
    候选值按从小到大排列，把字典序空间切成若干块；逐位累加块大小，找到
    包含 k 的块，令 k 变为块内相对位置后继续确定下一位。单次定位 O(m)，
    全程 O(n*m)，不枚举任何排列。
    """
    _, grouped, k, _ = _normalize(elements, k)
    counts = [c for _, c in grouped]
    m = len(grouped)
    remaining = sum(counts)
    total = multiset_permutation_count(counts)
    _check_k(k, total, "排列")

    answer = []
    while remaining > 0:
        for i in range(m):
            c = counts[i]
            if c == 0:
                continue
            block = total * c // remaining
            if k > block:
                k -= block
            else:
                answer.append(grouped[i][0])
                counts[i] -= 1
                remaining -= 1
                total = block
                break
    return tuple(answer)


def kth_combination(elements: Iterable, r: int, k: int) -> Tuple:
    """按字典序返回从 elements 中选 r 个的第 k 个多集合组合。

    元素先升序排序，答案是非降序列。确定某一位时，设上一位选定值的种类
    下标为 prev（该种类可能已经被取走 q 份），候选种类 j 从 prev 开始
    递增。固定本位为种类 j 后，剩余 L-1 个空位只能用第 j..m-1 种值填充，
    这一块的大小（即块内组合数）由后缀计数表 ways 给出：

    - j > prev 时，种类 j 尚未被取用过，块大小为
          ways[j][L-1] - ways[j+1][L-1-c_j]，
      即从「j 可取 0..c_j 份」中扣掉「j 恰好再取满 c_j 份」（加上本位的
      一份共 c_j+1 份，不可能）这一项，等价于 j 只剩 c_j-1 份。
    - j == prev 时，种类 j 已被取走 q 份，块大小为
          sum_{x=0..min(c_j-q-1, L-1)} ways[j+1][L-1-x]，
      用前缀和在 O(1) 内求出。

    块按候选值从小到大排列；累加块大小定位 k 所在块，再进入下一位。
    预处理 O(m*r)，定位 O(m*r)，不枚举任何组合。
    """
    _, grouped, k, r = _normalize(elements, k, r)
    counts = [c for _, c in grouped]
    m = len(grouped)
    total = multiset_combination_count(counts, r)
    _check_k(k, total, "组合")

    ways, pref = _suffix_tables(counts, r)
    answer = []
    prev = 0
    taken = 0  # 已从种类 prev 取走的份数
    slots = r
    while slots > 0:
        left = slots - 1
        for j in range(prev, m):
            if counts[j] - (taken if j == prev else 0) == 0:
                continue
            if j == prev:
                usable = counts[j] - taken - 1
                block = _range_sum(pref[j + 1], left - usable, left)
            else:
                block = ways[j][left] if left < len(ways[j]) else 0
                if left - counts[j] >= 0:
                    extra = ways[j + 1][left - counts[j]]
                    if left - counts[j] < len(ways[j + 1]):
                        block -= extra
            if k > block:
                k -= block
            else:
                answer.append(grouped[j][0])
                if j == prev:
                    taken += 1
                else:
                    prev = j
                    taken = 1
                slots -= 1
                break
    return tuple(answer)


def kth_multiset(elements: Iterable, k: int, kind: str, r: Optional[int] = None) -> Tuple:
    """统一入口。

    kind="combination"（或 "C"/"组合"）时必须给出 r，返回第 k 个长度为 r
    的组合；kind="permutation"（或 "P"/"排列"）时 r 被忽略，返回第 k 个
    全排列。
    """
    if kind in ("combination", "C", "c", "组合"):
        if r is None:
            raise KthError("组合问题必须提供 r")
        return kth_combination(elements, r, k)
    if kind in ("permutation", "P", "p", "排列"):
        return kth_permutation(elements, k)
    raise KthError("kind 只能是 'combination' 或 'permutation'")
