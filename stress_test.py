"""stress_test.py -- 快速实现与朴素实现的对拍 + 边界情形测试。

对拍口径：
- 合并顺序（每步合并的簇 id 对、新簇大小）必须完全一致；
- 距离序列：单链接要求逐位相等（两者都是点对距离的 min，无舍入差异），
  平均链接允许 1e-9 相对误差（Lance-Williams 递推与全量重算的浮点舍入路径不同）。

运行：python3 stress_test.py
"""

import math
import random
import sys

from hclust import linkage, linkage_naive, cut

FAILURES = []


def check(cond, msg):
    if not cond:
        FAILURES.append(msg)
        print("FAIL:", msg)


def _leaves(merges, n):
    """每个簇 id 对应的叶子（原始点）集合。"""
    mem = {i: (i,) for i in range(n)}
    for k, m in enumerate(merges):
        mem[n + k] = mem[m.id1] + mem[m.id2]
    return mem


def _structure(merges, n):
    """树状图结构：每次合并表示为两个子簇叶子集合（排序后的二元组）。"""
    mem = _leaves(merges, n)
    out = []
    for m in merges:
        a, b = mem[m.id1], mem[m.id2]
        out.append((a, b) if a <= b else (b, a))
    return sorted(out)


def validate_greedy(merges, points, method, tag):
    """与并列无关的正确性校验：重放合并历史，每步从原始点重算全部
    活跃簇间距离，验证 (a) 记录距离 == 真实距离；(b) 该距离是全局最小；
    (c) 距离序列单调不减；(d) 新簇大小正确。"""
    n = len(points)
    D, offsets = __import__("hclust")._build_matrix(points)

    def pd(i, j):
        return D[offsets[i] + j] if j > i else D[offsets[j] + i]

    average = method == "average"
    mem = {i: (i,) for i in range(n)}
    active = list(range(n))
    prev = -1.0
    for k, m in enumerate(merges):
        mi, mj = mem[m.id1], mem[m.id2]

        def cdist(a, b):
            if average:
                s = 0.0
                for p in a:
                    for q in b:
                        s += pd(p, q)
                return s / (len(a) * len(b))
            d = math.inf
            for p in a:
                for q in b:
                    v = pd(p, q)
                    if v < d:
                        d = v
            return d

        true_d = cdist(mi, mj)
        check(math.isclose(m.distance, true_d, rel_tol=1e-9, abs_tol=1e-12),
              "%s: step %d recorded %.17g != true %.17g" % (tag, k, m.distance, true_d))
        gmin = math.inf
        for x in range(len(active)):
            for y in range(x + 1, len(active)):
                v = cdist(mem[active[x]], mem[active[y]])
                if v < gmin:
                    gmin = v
        check(m.distance <= gmin + 1e-9,
              "%s: step %d merges at %.17g but min pair is %.17g" % (tag, k, m.distance, gmin))
        check(m.distance >= prev - 1e-12, "%s: step %d not monotone" % (tag, k))
        prev = m.distance
        nid = n + k
        mem[nid] = mi + mj
        check(m.size == len(mem[nid]), "%s: step %d size" % (tag, k))
        active.remove(m.id1)
        active.remove(m.id2)
        active.append(nid)


def compare(points, method, exact_dist, tag, strict_order):
    """strict_order=True（无并列数据）：合并顺序与距离序列逐步一致。
    strict_order=False（含并列数据）：树状图结构一致 + 有序距离序列一致。"""
    fast = linkage(points, method)
    naive = linkage_naive(points, method)
    if len(fast) != len(naive):
        check(False, "%s: merge count %d != %d" % (tag, len(fast), len(naive)))
        return

    def d_eq(x, y):
        if exact_dist:
            return x == y
        return math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12)

    # 树状图结构必须完全一致（任何数据都检查）
    check(_structure(fast, len(points)) == _structure(naive, len(points)),
          "%s: dendrogram structure differs" % tag)
    # 有序距离序列必须一致
    df = sorted(m.distance for m in fast)
    dg = sorted(m.distance for m in naive)
    check(all(d_eq(x, y) for x, y in zip(df, dg)),
          "%s: sorted distance sequences differ" % tag)
    if strict_order:
        for k, (f, g) in enumerate(zip(fast, naive)):
            if (f.id1, f.id2, f.size) != (g.id1, g.id2, g.size):
                check(False, "%s: step %d order (%d,%d,s%d) != (%d,%d,s%d)"
                      % (tag, k, f.id1, f.id2, f.size, g.id1, g.id2, g.size))
                break
            if not d_eq(f.distance, g.distance):
                check(False, "%s: step %d distance %.17g != %.17g"
                      % (tag, k, f.distance, g.distance))
                break


def test_random_clouds():
    """随机浮点数据（无并列），两种链接方式，合并顺序与距离序列严格对拍。"""
    rng = random.Random(20260928)
    cases = 0
    for trial in range(120):
        n = rng.randint(2, 40)
        dim = rng.randint(1, 4)
        pts = [(tuple(rng.uniform(-100, 100) for _ in range(dim))) for _ in range(n)]
        for method in ("single", "average"):
            compare(pts, method, exact_dist=(method == "single"),
                    tag="random#%d/%s" % (trial, method), strict_order=True)
            validate_greedy(linkage(pts, method), pts, method,
                            "random#%d/%s/fast" % (trial, method))
            cases += 1
    # 一个较大的随机用例
    pts = [(rng.uniform(0, 1000), rng.uniform(0, 1000)) for _ in range(250)]
    for method in ("single", "average"):
        compare(pts, method, exact_dist=(method == "single"),
                tag="random-big/" + method, strict_order=True)
        cases += 1
    print("random clouds: %d cases done" % cases)


def test_tie_grids():
    """整数网格 + 重复点，制造大量距离并列。"""
    rng = random.Random(7)
    cases = 0
    for trial in range(60):
        n = rng.randint(2, 25)
        pts = [(rng.randint(0, 4), rng.randint(0, 4)) for _ in range(n)]
        for method in ("single", "average"):
            tag = "grid#%d/%s" % (trial, method)
            validate_greedy(linkage(pts, method), pts, method, tag + "/fast")
            validate_greedy(linkage_naive(pts, method), pts, method, tag + "/naive")
        cases += 2
    print("tie grids: %d cases done" % cases)


def test_all_identical():
    for n in (2, 3, 10, 50):
        pts = [(3.0, -1.5)] * n
        for method in ("single", "average"):
            fast = linkage(pts, method)
            naive = linkage_naive(pts, method)
            check(len(fast) == n - 1, "identical n=%d: merge count" % n)
            check(all(m.distance == 0.0 for m in fast),
                  "identical n=%d/%s: all distances zero" % (n, method))
            check(sorted(m.size for m in fast) == sorted(m.size for m in naive),
                  "identical n=%d/%s: sizes" % (n, method))
            check(_structure(fast, n) == _structure(naive, n),
                  "identical n=%d/%s: structure" % (n, method))
    print("all identical: done")


def test_single_point():
    check(linkage([(1, 2)], "single") == [], "single point: empty history")
    check(linkage([(1, 2)], "average") == [], "single point: empty history")
    check(linkage_naive([(1, 2)], "single") == [], "single point naive: empty")
    check(linkage([], "single") == [], "empty input: empty history")
    print("single point / empty: done")


def test_known_tie():
    """两对并列：手算期望值，快速与朴素实现都必须精确命中。"""
    pts = [(0.0,), (2.0,), (10.0,), (12.0,)]
    expect_single = [(0, 1, 2.0, 2), (2, 3, 2.0, 2), (4, 5, 8.0, 4)]
    expect_avg = [(0, 1, 2.0, 2), (2, 3, 2.0, 2), (4, 5, 10.0, 4)]
    for impl in (linkage, linkage_naive):
        got = [tuple(m) for m in impl(pts, "single")]
        check(got == expect_single, "tie single: %r" % (got,))
        got = [tuple(m) for m in impl(pts, "average")]
        check(got == expect_avg, "tie average: %r" % (got,))
    # 该用例下两者合并顺序也逐步一致
    compare(pts, "single", exact_dist=True, tag="tie/single", strict_order=True)
    compare(pts, "average", exact_dist=True, tag="tie/average", strict_order=True)
    print("known tie: done")


def test_cut():
    pts = [(0, 0), (1, 0), (10, 0), (11, 0)]
    merges = linkage(pts, "single")
    check(cut(merges, 4, 2.0) == [0, 0, 1, 1], "cut t=2")
    check(cut(merges, 4, 100.0) == [0, 0, 0, 0], "cut t=100")
    check(len(set(cut(merges, 4, 0.5))) == 4, "cut t=0.5 -> 4 clusters")
    print("cut: done")


def main():
    test_single_point()
    test_known_tie()
    test_all_identical()
    test_cut()
    test_tie_grids()
    test_random_clouds()
    if FAILURES:
        print("\n%d FAILURES" % len(FAILURES))
        sys.exit(1)
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
