"""原始（有缺陷）的合并实现 —— 仅用于复现不一致问题，请勿在生产使用。

缺陷：
1. 只比较时间戳，时间戳相同（多端时钟同步或批量导入时很常见）时
   结果取决于更新到达顺序 —— 不可交换，导致各端收敛到不同值。
2. 来源标识缺失（None）时没有任何决胜手段。
3. 版本向量按字典遍历顺序合并，不同端可能得到不同结果。
"""


def merge_field_buggy(current, incoming):
    """current / incoming: (value, timestamp, source) 三元组，source 可能为 None。

    返回合并后的三元组。时间戳大的赢；相等时保留 current —— 因此
    merge(a, b) != merge(b, a)，合并结果依赖到达顺序。
    """
    if current is None:
        return incoming
    if incoming is None:
        return current
    if incoming[1] > current[1]:
        return incoming
    return current


def merge_version_vector_buggy(vv_a, vv_b):
    """按 vv_a 的键遍历合并；键为 None 时直接丢弃，导致版本向量不一致。"""
    merged = dict(vv_a)
    for source, counter in vv_b.items():
        if source is None:
            continue  # 缺陷：来源缺失的更新被静默丢弃
        if counter > merged.get(source, 0):
            merged[source] = counter
    return merged
