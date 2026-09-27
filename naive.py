"""朴素参考实现：仅用于对拍验证，不做任何性能优化。"""


def naive_suffix_array(s: bytes) -> list[int]:
    """直接对所有后缀排序。比较两个后缀代价 O(n)，总复杂度 O(n^2 log n)。"""
    return sorted(range(len(s)), key=lambda i: s[i:])


def naive_lcp_array(s: bytes, sa: list[int]) -> list[int]:
    """按定义逐对暴力比较相邻后缀。"""
    n = len(s)
    lcp = [0] * n
    for idx in range(1, n):
        a, b = sa[idx - 1], sa[idx]
        h = 0
        while a + h < n and b + h < n and s[a + h] == s[b + h]:
            h += 1
        lcp[idx] = h
    return lcp


def naive_lcp_of_suffixes(s: bytes, i: int, j: int) -> int:
    """暴力逐字节比较后缀 i 与后缀 j。"""
    h = 0
    while i + h < len(s) and j + h < len(s) and s[i + h] == s[j + h]:
        h += 1
    return h


def naive_longest_repeated_substring(s: bytes) -> tuple[int, int]:
    """暴力枚举所有后缀对求 LCP 最大值。返回 (长度, 一个出现位置)。"""
    n = len(s)
    best_len, best_pos = 0, 0
    for i in range(n):
        for j in range(i + 1, n):
            h = naive_lcp_of_suffixes(s, i, j)
            if h > best_len:
                best_len, best_pos = h, i
    return (best_len, best_pos)
