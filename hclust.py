"""hclust.py -- 凝聚式层次聚类（仅标准库）。

支持单链接（single linkage）与平均链接（average linkage）。

- linkage():       快速实现。最近邻链（nearest-neighbor chain）+ Lance-Williams
                   距离更新。时间 O(n^2)，距离矩阵用 array('d') 紧凑存储，
                   簇槽位复用，矩阵规模始终为 n*(n-1)/2 个 float64。
- linkage_naive(): 朴素实现。每一步从原始点集重算全部簇间距离，用于对拍。
- cut():           按距离阈值切分合并历史，得到每个点的簇标签。

合并历史：Merge(id1, id2, distance, size) 列表，与 scipy 的 linkage 矩阵同构。
原始点 id 为 0..n-1，第 k 次合并产生的新簇 id 为 n+k。distance 单调不减，
可直接用于绘制树状图与选择切分位置。
"""

from array import array
from bisect import insort
from collections import namedtuple
from math import inf, sqrt

__all__ = ["Merge", "linkage", "linkage_naive", "cut"]

Merge = namedtuple("Merge", ["id1", "id2", "distance", "size"])

_METHODS = ("single", "average")


def _condensed_offsets(n):
    # 下标 (i, j)（i < j）在压缩距离矩阵中的位置为 offsets[i] + j
    return [i * n - (i * (i + 1)) // 2 - i - 1 for i in range(n)]


def _build_matrix(points):
    """构造点间欧氏距离的压缩（上三角）矩阵，float64。"""
    n = len(points)
    offsets = _condensed_offsets(n)
    D = array("d", [0.0]) * (n * (n - 1) // 2)
    pts = [list(p) for p in points]
    for i in range(n - 1):
        pi = pts[i]
        base = offsets[i]
        for j in range(i + 1, n):
            pj = pts[j]
            s = 0.0
            for a, b in zip(pi, pj):
                d = a - b
                s += d * d
            D[base + j] = sqrt(s)
    return D, offsets


def linkage(points, method="single"):
    """快速层次聚类，返回合并历史 List[Merge]。

    method: "single"（单链接，簇间距离取最近点对）
            "average"（平均链接，簇间距离取全部点对距离的平均，即 UPGMA）
    """
    if method not in _METHODS:
        raise ValueError("method must be one of %r" % (_METHODS,))
    n = len(points)
    if n < 2:
        return []
    D, offsets = _build_matrix(points)
    merges = _nn_chain(D, offsets, n, method == "average")
    return _canonicalize(merges, n)


def _nn_chain(D, offsets, n, average):
    """最近邻链算法。single/average 链接均满足可约性，结果与逐步全局
    取最小对的经典凝聚算法完全一致（无并列时合并顺序也一致）。"""
    total = 2 * n - 1
    slot = list(range(n)) + [0] * (n - 1)  # 簇 id -> 距离矩阵槽位（合并时复用）
    sizes = [1] * n + [0] * (n - 1)
    active = list(range(n))                # 升序的存活簇 id
    merges = []
    chain = []
    next_id = n

    while len(active) > 1:
        if not chain:
            chain.append(active[0])
        a = chain[-1]
        sa = slot[a]
        off_a = offsets[sa]
        # 找 a 的最近邻（距离并列时取 id 最小者，保证确定性）
        best = -1
        best_d = inf
        for k in active:
            if k == a:
                continue
            sk = slot[k]
            d = D[off_a + sk] if sk > sa else D[offsets[sk] + sa]
            if d < best_d:
                best_d = d
                best = k
        if len(chain) >= 2 and chain[-2] == best:
            # 链端互指，合并 a 与 best
            b = best
            chain.pop()
            chain.pop()
            sb = slot[b]
            na, nb = sizes[a], sizes[b]
            # Lance-Williams 更新：新簇复用槽位 sa，将其到其余簇的距离改写
            for k in active:
                if k == a or k == b:
                    continue
                sk = slot[k]
                idx_a = off_a + sk if sk > sa else offsets[sk] + sa
                idx_b = offsets[sb] + sk if sk > sb else offsets[sk] + sb
                da = D[idx_a]
                db = D[idx_b]
                if average:
                    D[idx_a] = (na * da + nb * db) / (na + nb)
                elif db < da:
                    D[idx_a] = db
            active.remove(a)
            active.remove(b)
            active.append(next_id)  # id 递增，直接追加即保持升序
            slot[next_id] = sa
            sizes[next_id] = na + nb
            merges.append(Merge(min(a, b), max(a, b), best_d, na + nb))
            next_id += 1
        else:
            chain.append(best)
    return merges


def _canonicalize(merges, n):
    """把 NN-chain 的合并历史整理为 scipy 风格：距离单调不减、
    第 k 步新簇 id 为 n+k。NN-chain 输出本身已是拓扑序（子簇先于父簇
    被合并），按距离稳定排序后该性质保持；并列时顺序确定。"""
    order = sorted(range(len(merges)), key=lambda k: merges[k].distance)
    remap = {}
    out = []
    for k, old in enumerate(order):
        m = merges[old]
        id1 = remap.get(m.id1, m.id1)
        id2 = remap.get(m.id2, m.id2)
        if id1 > id2:
            id1, id2 = id2, id1
        remap[n + old] = n + k
        out.append(Merge(id1, id2, m.distance, m.size))
    return out


def linkage_naive(points, method="single"):
    """朴素实现：每一步重算全部簇间距离（由成员点对距离现算），
    取全局最小对（距离并列时取 (id1, id2) 字典序最小者）。用于对拍。"""
    if method not in _METHODS:
        raise ValueError("method must be one of %r" % (_METHODS,))
    n = len(points)
    if n < 2:
        return []
    average = method == "average"
    D, offsets = _build_matrix(points)  # 点对距离只算一次；簇间距离每步全量重算

    def pair_dist(i, j):
        return D[offsets[i] + j] if j > i else D[offsets[j] + i]

    members = {i: (i,) for i in range(n)}  # 簇 id -> 成员点
    active = list(range(n))
    merges = []
    next_id = n
    while len(active) > 1:
        best = None
        best_d = inf
        for x in range(len(active)):
            mi = members[active[x]]
            for y in range(x + 1, len(active)):
                mj = members[active[y]]
                if average:
                    s = 0.0
                    for p in mi:
                        for q in mj:
                            s += pair_dist(p, q)
                    d = s / (len(mi) * len(mj))
                else:
                    d = inf
                    for p in mi:
                        for q in mj:
                            dpq = pair_dist(p, q)
                            if dpq < d:
                                d = dpq
                if d < best_d:  # 严格小于：并列时保留字典序最小的一对
                    best_d = d
                    best = (active[x], active[y])
        i, j = best
        members[next_id] = members.pop(i) + members.pop(j)
        active.remove(i)
        active.remove(j)
        active.append(next_id)
        merges.append(Merge(i, j, best_d, len(members[next_id])))
        next_id += 1
    return merges


def cut(merges, n, threshold):
    """按距离阈值切分合并历史，返回长度为 n 的簇标签列表（0..k-1）。

    合并距离单调不减，故只合并 distance <= threshold 的边。
    """
    parent = list(range(2 * n - 1 if n > 0 else 0))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for k, m in enumerate(merges):
        if m.distance > threshold:
            break
        parent[find(m.id1)] = find(m.id2)
        parent[find(m.id2)] = n + k
        parent[n + k] = n + k
    labels = {}
    out = []
    for i in range(n):
        r = find(i)
        if r not in labels:
            labels[r] = len(labels)
        out.append(labels[r])
    return out
