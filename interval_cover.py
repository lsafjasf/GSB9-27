"""
最小区间覆盖（Interval Point/Range Cover）—— 库 + 自测
=====================================================

问题
----
给定目标范围 [L, R]（闭区间）与 n 个闭区间 [s_i, e_i]（允许 s_i == e_i，
即零长度区间），选出**个数最少**的区间集合，使其并集完整覆盖 [L, R]。
若无法覆盖，报告断点位置。

贪心规则
--------
1. 令 reach = L，含义："[L, reach) 已被已选区间连续覆盖，reach 是下一个
   待覆盖点"（不变式）。
2. 每轮在所有满足 s_i <= reach 的区间中，选出右端点 e_i 最大者（记为 best），
   将其加入答案，令 reach = max(reach, e_best)。
3. 若某一轮中所有 s_i <= reach 的区间都满足 e_i <= reach（无法把覆盖前沿
   向右推进），则失败：断点（无法被覆盖点集的下确界）就是当前 reach。
4. 当 reach >= R 时停止，已选集合即为最优解。

实现上用"按左端点排序 + 指针扫描"，每轮只考察新进入 s_i <= reach 的区间，
总复杂度 O(n log n)（排序主导），选择阶段 O(n)。

正确性论证（交换论证）
---------------------
记贪心第 k 步结束后的覆盖前沿为 g_k（g_0 = L），任意可行解 S 按"能连续
推进前沿"的顺序重排后第 k 步能达到的前沿为 o_k。

**引理（贪心保持领先）**：对任意 k >= 0，存在某个最优解，其前 k 步能达到的
前沿 o_k 满足 o_k <= g_k。

**证明（对 k 归纳 / 交换）**：
- 基例 k = 0：o_0 = L = g_0，平凡成立。
- 归纳步：设存在最优解 S，其前 k-1 步与贪心一致（或前沿不超过 g_{k-1}）。
  S 的第 k 个区间 [s, e] 要接续覆盖，必须满足 s <= o_{k-1} <= g_{k-1}，
  即它也是贪心第 k 步的**候选**（s <= g_{k-1}）。贪心在所有候选中取右端点
  最大者，故 g_k = max{e_i : s_i <= g_{k-1}} >= e = o_k。
  并且可以把 S 中第 k 个区间**交换**为贪心的选择：贪心区间起点 <= g_{k-1}
  保证了与已覆盖前缀相接不断裂，右端点更大只会覆盖 S 第 k 个区间覆盖范围
  的超集，S 中后续区间依然能接续。交换后解的大小不变，仍是最优解。
  由归纳法，引理成立。∎

**定理**：贪心在可覆盖时输出的区间数等于最优值；在不可覆盖时正确报告失败。

**证明**：
- 可覆盖性判定：若贪心在某步无法推进（所有候选 e_i <= reach），则由引理，
  任何解（包括最优解）在该步的前沿 o <= g = reach 同样无法越过 reach，
  故问题本身不可覆盖；反之若问题可覆盖，贪心每步都能推进且前沿不减、
  每步严格增大（候选中存在 e > reach 的区间才会被选中推进），
  区间右端点有上界 R，故有限步内 reach >= R 停止。
- 最优性：设贪心用了 m 个区间，最优解用了 m* 个。由引理，最优解前 m*
  步前沿 o_{m*} <= g_{m*}。可覆盖意味着 o_{m*} >= R（最优解 m* 步内
  必须覆盖到 R），而 g 序列严格递增，故 g_{m*} >= R，即贪心至多 m* 步
  就已覆盖 [L, R]，m <= m*。又 m* 是最优值，m >= m*，故 m = m*。∎

**断点语义**：无法覆盖时返回的 gap 是"无法被覆盖点集在 [L, R] 内的下确界"，
即从 L 出发最多能连续覆盖到 gap（gap 本身是否被覆盖取决于是否存在
[s, gap] 型区间；gap 之后立即断开）。若 L 本身就不被任何区间覆盖，
gap == L。

边界情形
--------
- 目标为空（L > R）：不需要任何区间，返回空集合。
- 零长度目标（L == R）：闭区间语义下点 L 必须真被包含，答案为 1 个
  覆盖该点的区间（零长度区间 [L, L] 即可）；主循环的不变式 [L, reach)
  对 L == R 是空集，故该情形在入口单独处理。
- 零长度区间 [x, x]：恰好覆盖点 x，可参与覆盖。
- 完全嵌套区间：贪心每步取右端点最大者，自动选外层、跳过被包含者。
- 端点恰好相接（e_i == s_j）：闭区间语义下相接即连续，覆盖不断裂。

自测（python3 interval_cover.py）
---------------------------------
1. 固定边界用例（含上述全部边界情形）；
2. 与 O(2^n) 穷举最优对拍：大量随机小规模实例，比较区间个数，
   并校验贪心所选集合确实完整覆盖目标；
3. 性能测试：10 万随机区间计时。
"""

from __future__ import annotations

import itertools
import random
import time

Interval = tuple  # (start, end)，闭区间，要求 start <= end


def min_interval_cover(L, R, intervals):
    """返回 (ok, chosen, gap)。

    ok=True 时：chosen 是覆盖 [L, R] 的最少区间列表（保持原对象），gap 为 None。
    ok=False 时：chosen 为 None，gap 为断点（无法被覆盖点集的下确界，
                 即从 L 最多能连续覆盖到的位置；L 本身不可覆盖时 gap == L）。
    目标为空（L > R）时：ok=True，chosen=[]，gap=None。
    """
    if L > R:
        return True, [], None

    if L == R:
        # 退化点目标：闭区间语义下点 L 本身必须被某个区间包含
        point_hits = [iv for iv in intervals if iv[0] <= L <= iv[1]]
        if not point_hits:
            return False, None, L
        return True, [max(point_hits, key=lambda iv: iv[1])], None

    # 按左端点升序排序；零长度区间、负坐标均无需特判
    ivs = sorted(intervals, key=lambda iv: (iv[0], iv[1]))
    n = len(ivs)

    chosen = []
    reach = L          # 不变式：[L, reach) 已被连续覆盖
    i = 0              # 扫描指针：ivs[:i] 的左端点都 <= 上一轮的 reach
    while reach < R:
        best_end = reach
        best_idx = -1
        while i < n and ivs[i][0] <= reach:
            if ivs[i][1] > best_end:
                best_end = ivs[i][1]
                best_idx = i
            i += 1
        if best_idx < 0:
            # 所有 s <= reach 的区间都无法把前沿推过 reach
            return False, None, reach
        chosen.append(ivs[best_idx])
        reach = best_end
    return True, chosen, None


# ---------------------------------------------------------------- 自测辅助

def _is_covered(L, R, subset):
    """校验 subset 的并集是否完整覆盖闭区间 [L, R]（扫描线）。"""
    if L > R:
        return True
    if L == R:
        return any(s <= L <= e for s, e in subset)
    reach = L
    for s, e in sorted(subset):
        if s > reach:
            return False
        if e > reach:
            reach = e
        if reach >= R:
            return True
    return reach >= R


def _brute_force_count(L, R, intervals):
    """穷举所有子集，返回覆盖 [L, R] 所需的最少区间数；不可覆盖返回 None。"""
    if L > R:
        return 0
    n = len(intervals)
    for k in range(n + 1):
        for combo in itertools.combinations(range(n), k):
            if _is_covered(L, R, [intervals[j] for j in combo]):
                return k
    return None


# ---------------------------------------------------------------- 边界用例

def test_edge_cases():
    # 1) 目标为空：不需要任何区间
    ok, chosen, gap = min_interval_cover(5, 1, [(0, 10)])
    assert ok and chosen == [] and gap is None

    # 2) 零长度目标 + 零长度区间恰好命中
    ok, chosen, gap = min_interval_cover(3, 3, [(3, 3)])
    assert ok and len(chosen) == 1 and chosen[0] == (3, 3)

    # 3) 零长度目标但无区间覆盖该点 -> 断点即该点
    ok, chosen, gap = min_interval_cover(3, 3, [(0, 2), (4, 9)])
    assert not ok and gap == 3

    # 4) 完全嵌套：必须选最外层 1 个，贪心不被内层迷惑
    ok, chosen, gap = min_interval_cover(0, 10, [(0, 10), (2, 8), (4, 6), (5, 5)])
    assert ok and len(chosen) == 1 and chosen[0] == (0, 10)

    # 5) 端点恰好相接（闭区间连续）：[0,5]+[5,10] 覆盖 [0,10]
    ok, chosen, gap = min_interval_cover(0, 10, [(0, 5), (5, 10)])
    assert ok and len(chosen) == 2

    # 6) 端点不相接（留缝）：[0,5] 与 [6,10] 盖不住 (5,6)，断点为 5
    ok, chosen, gap = min_interval_cover(0, 10, [(0, 5), (6, 10)])
    assert not ok and gap == 5

    # 7) 起点之外才有区间：L 本身不可覆盖，gap == L
    ok, chosen, gap = min_interval_cover(0, 10, [(2, 12)])
    assert not ok and gap == 0

    # 8) 区间列表为空
    ok, chosen, gap = min_interval_cover(0, 10, [])
    assert not ok and gap == 0

    # 9) 干扰项众多的例子：最优为 (1,4)+(4,10) 共 2 段
    ivs9 = [(1, 2), (2, 3), (3, 4), (1, 4), (4, 10), (2, 5)]
    ok, chosen, gap = min_interval_cover(1, 10, ivs9)
    assert ok and len(chosen) == 2 and _is_covered(1, 10, chosen)
    assert len(chosen) == _brute_force_count(1, 10, ivs9)

    # 10) 负坐标与零长度区间混合
    ok, chosen, gap = min_interval_cover(-5, 5, [(-5, -5), (-5, 0), (0, 5)])
    assert ok and len(chosen) == 2 and _is_covered(-5, 5, chosen)

    # 11) 不可覆盖时断点是最远连续前沿，而非第一个失败区间的起点
    ok, chosen, gap = min_interval_cover(0, 100, [(0, 30), (10, 50), (60, 100)])
    assert not ok and gap == 50

    print("[PASS] 边界用例 11 组全部通过")


# ---------------------------------------------------------------- 穷举对拍

def test_against_brute_force(trials=3000, seed=20260927):
    rng = random.Random(seed)
    for t in range(trials):
        n = rng.randint(0, 9)
        L = rng.randint(-5, 5)
        R = L + rng.randint(0, 8)          # 含零长度目标
        intervals = []
        for _ in range(n):
            a = rng.randint(-6, 11)
            b = a + rng.randint(0, 6)      # 含零长度区间；嵌套自然出现
            intervals.append((a, b))

        ok, chosen, gap = min_interval_cover(L, R, intervals)
        best = _brute_force_count(L, R, intervals)

        if best is None:
            assert not ok, f"第{t}例：穷举不可覆盖但贪心称可覆盖"
            # 断点合法性：gap 之前可覆盖，且不存在越过 gap 的连续覆盖
            assert L <= gap <= R or gap == L
            assert not _is_covered(L, R, intervals)
        else:
            assert ok, f"第{t}例：穷举可覆盖但贪心失败, gap={gap}"
            assert len(chosen) == best, (
                f"第{t}例：贪心 {len(chosen)} != 最优 {best}, "
                f"L={L} R={R} intervals={intervals}")
            assert _is_covered(L, R, chosen), f"第{t}例：所选集合未完整覆盖"
    print(f"[PASS] 穷举对拍 {trials} 组随机小规模实例：数量全部等于最优值")


# ---------------------------------------------------------------- 性能测试

def test_performance(n=100_000, seed=42):
    rng = random.Random(seed)
    L, R = 0, 1_000_000
    intervals = []
    for _ in range(n):
        a = rng.randint(-1000, R)
        b = a + rng.randint(0, 2000)
        intervals.append((a, b))

    t0 = time.perf_counter()
    ok, chosen, gap = min_interval_cover(L, R, intervals)
    t1 = time.perf_counter()
    assert ok, "性能用例应可覆盖"
    assert _is_covered(L, R, chosen)
    print(f"[PERF] n={n:,} 区间：求解+排序耗时 {(t1 - t0) * 1000:.2f} ms，"
          f"选出 {len(chosen)} 个区间，覆盖校验通过")


if __name__ == "__main__":
    test_edge_cases()
    test_against_brute_force()
    test_performance()
    print("全部测试通过。")
