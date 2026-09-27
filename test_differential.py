"""自测：边界用例 + 小规模随机穷举对拍。

运行：python3 test_differential.py
"""

from __future__ import annotations

import random
import time

from bitmask_dp import (
    InfeasibleError,
    KnapsackError,
    MemoryLimitExceeded,
    memory_bound_bytes,
    solve,
    state_count,
)
from brute_force import solve_bruteforce


def _assert_same(sol, bf, label):
    assert sol.feasible == bf.feasible, label
    assert sol.value == bf.value, (label, sol.value, bf.value)
    # 两边都采用“同值取最小 mask”，mask 必须完全一致（即“一个最优解”一致）
    assert sol.mask == bf.mask, (
        f"{label}: dp mask={bin(sol.mask)} value={sol.value} vs "
        f"bf mask={bin(bf.mask)} value={bf.value}"
    )
    assert tuple(sorted(sol.selected)) == tuple(
        i for i in range(bf.mask.bit_length() + 1) if (bf.mask >> i) & 1
    )


def test_empty_collection():
    """空集合：没有任何元素，最优为空集，价值 0。"""
    sol = solve([], 10)
    bf = solve_bruteforce([], 10)
    assert sol.value == 0 and sol.mask == 0 and sol.selected == ()
    _assert_same(sol, bf, "empty")
    print("[ok] 空集合 -> 空集最优, value=0")


def test_select_all():
    """全部可选：总消耗恰好等于容量（或更松），必须全部选中。"""
    items = [(3, 2), (5, 3), (7, 5)]
    sol = solve(items, 10)          # 总重恰好 10
    assert sol.value == 15 and sol.mask == 0b111 and list(sol.selected) == [0, 1, 2]
    sol_loose = solve(items, 100)   # 容量极松
    assert sol_loose.mask == 0b111
    bf = solve_bruteforce(items, 10)
    _assert_same(sol, bf, "all-fit")
    print("[ok] 全部可选 -> mask=0b111, value=15")


def test_conflicting_constraints():
    """约束互相冲突：

    1) 单维：容量为 0，任何带正重量的元素都不能选（只能空集）。
    2) 二维：两个元素各自只满足一维，无法同时选且单维容量互斥安排，
       冲突体现在“想要的组合必然撑破某一维”。
    3) 负容量：连空集都不可行 -> InfeasibleError。
    """
    # 1) 容量 0
    items = [(5, 1), (6, 1)]
    sol = solve(items, 0)
    assert sol.value == 0 and sol.mask == 0
    bf = solve_bruteforce(items, 0)
    _assert_same(sol, bf, "cap-zero")

    # 2) 二维冲突：A 用满 d0，B 用满 d1；若同时选则两维都爆；
    #    单独都可行。再放一个“两个维度都很重”的元素，制造互斥局面。
    #    容量 (4, 4)：A=(3,[4,1]) 价值 10，B=(3,[1,4]) 价值 10，
    #    C=(20,[5,5])：单看就放不下，与任何人组合更放不下，
    #    于是只能在 A/B 中二选一（等价值，取 mask 较小的 A）。
    items2 = [(10, [4, 1]), (10, [1, 4]), (20, [5, 5])]
    caps2 = [4, 4]
    sol2 = solve(items2, caps2)
    bf2 = solve_bruteforce(items2, caps2)
    _assert_same(sol2, bf2, "2d-conflict")
    assert sol2.value == 10  # 只能拿 A 或 B，且同值取最小 mask -> A
    assert sol2.mask == 0b001

    # 3) 负容量 -> 连空集都不可行
    try:
        solve([(1, 1)], -1)
        raise AssertionError("应当抛出 InfeasibleError")
    except InfeasibleError:
        pass
    try:
        solve_bruteforce([(1, 1)], -1)
        raise AssertionError("穷举应当抛出 InfeasibleError")
    except InfeasibleError:
        pass
    print("[ok] 约束冲突（容量0/二维互斥/负容量不可行）")


def test_equal_values_and_weights():
    """存在相同价值（以及相同重量）时，结果确定且与穷举一致。"""
    # 多个等价值方案：{0} 与 {1} 都 value=5，应取 mask 较小者；
    # 全选超重。
    items = [(5, 6), (5, 6), (5, 6)]
    sol = solve(items, 6)
    assert sol.value == 5 and sol.mask == 0b001, list(sol.selected)

    # 不同组合等价值：{0,1} value=7 与 {2} value=7，容量只允许其中一种
    # weights 7 与 7，容量 7 -> mask 0b011 vs 0b100，取小 mask 0b011
    items = [(3, 3), (4, 4), (7, 7)]
    sol = solve(items, 7)
    assert sol.value == 7 and sol.mask == 0b011, bin(sol.mask)
    bf = solve_bruteforce(items, 7)
    _assert_same(sol, bf, "tie-different-subsets")
    print("[ok] 相同权值/相同价值 -> 确定性 tie-break, value=7, mask=0b011")


def test_zero_weight_zero_value():
    """零重量零价值元素：可选可不选，同值时取最小 mask（不选）。"""
    items = [(0, 0), (5, 3), (0, 0)]
    sol = solve(items, 3)
    assert sol.value == 5
    # 元素 0 零值零重，选它不影响价值，同值取小 mask -> 不选
    assert sol.mask == 0b010, bin(sol.mask)
    bf = solve_bruteforce(items, 3)
    _assert_same(sol, bf, "zeros")
    print("[ok] 零重量零价值 tie-break -> mask=0b010")


def test_multidim():
    """二维容量基本正确性对拍。"""
    items = [(10, [5, 2]), (8, [3, 4]), (12, [4, 5]), (6, [1, 1])]
    for caps in ([8, 6], [10, 10], [6, 7], [0, 100], [100, 0]):
        sol = solve(items, list(caps))
        bf = solve_bruteforce(items, list(caps))
        _assert_same(sol, bf, f"2d-{caps}")
    print("[ok] 二维容量多组容量对拍一致")


def test_memory_guard():
    """内存上界评估与拒绝逻辑。"""
    assert state_count(0) == 1
    assert state_count(10) == 1024
    # m=1: (m+1)*8 = 16 字节/状态
    assert memory_bound_bytes(10, 1) == 1024 * 16
    assert memory_bound_bytes(20, 1) == (1 << 20) * 16

    # 预算故意给小：n=16 需要 65536*16 = 1 MiB，预算 512 KiB -> 拒绝
    try:
        solve([(1, 1)] * 16, 16, mem_budget_bytes=512 * 1024)
        raise AssertionError("应当 MemoryLimitExceeded")
    except MemoryLimitExceeded:
        pass

    # 同样的小预算下小规模仍然能算
    sol = solve([(1, 1)] * 5, 5, mem_budget_bytes=512 * 1024)
    assert sol.value == 5

    # 硬性 n 上限
    try:
        solve([(1, 1)] * 64, 64, mem_budget_bytes=10**18)
        raise AssertionError("n>63 应被硬上限拒绝")
    except MemoryLimitExceeded:
        pass
    print("[ok] 内存上界评估与超预算拒绝")


def test_input_validation():
    for bad in ([(1, -1)], [(1, [1, 2])], [(-1, 1)], "notalist"):
        try:
            if bad == "notalist":
                solve(bad, 5)
            else:
                solve(bad, 5)
            raise AssertionError(f"非法输入未被拒绝: {bad}")
        except KnapsackError:
            pass
    print("[ok] 输入校验")


def test_randomized_differential(rounds=3000, seed=20260928):
    """随机实例：DP 与穷举的最优值和同一个最优解必须一致。"""
    rng = random.Random(seed)
    t0 = time.perf_counter()
    for r in range(rounds):
        n = rng.randint(0, 9)
        m = rng.choice([1, 1, 1, 2, 3])
        items = []
        for _ in range(n):
            v = rng.choice([0, 1, 1, 2, 5, 10])  # 刻意包含 0 与重复值
            w = [rng.choice([0, 1, 1, 2, 3, 5]) for _ in range(m)]
            items.append((v, w if m > 1 else w[0]))
        caps = [rng.choice([0, 1, 2, 3, 6, 10, 100]) for _ in range(m)]  # 含 0 制造冲突
        cap_arg = caps[0] if m == 1 else caps

        sol = solve(items, cap_arg)
        bf = solve_bruteforce(items, cap_arg)
        _assert_same(sol, bf, f"random round {r}: {items} caps={caps}")
    dt = time.perf_counter() - t0
    print(f"[ok] 随机对拍 {rounds} 轮（n<=9, m<=3，含0值/重复值/0容量）全部一致，"
          f"耗时 {dt:.2f}s")


def test_large_dp_vs_known_bound():
    """大一点的实例（n=20）只跑 DP，用一个已知上界做健全性检查：
    价值不超过全部元素价值之和，且消耗不超容量。"""
    rng = random.Random(42)
    n = 20
    items = [(rng.randint(1, 20), rng.randint(1, 8)) for _ in range(n)]
    cap = 40
    sol = solve(items, cap, mem_budget_bytes=64 * 1024 * 1024)
    assert 0 <= sol.value <= sum(v for v, _ in items)
    assert sum(w for _, w in [items[i] for i in sol.selected]) <= cap
    print(f"[ok] n=20 健全性检查 value={sol.value} mask 位数={sol.mask.bit_count()}")


if __name__ == "__main__":
    test_empty_collection()
    test_select_all()
    test_conflicting_constraints()
    test_equal_values_and_weights()
    test_zero_weight_zero_value()
    test_multidim()
    test_memory_guard()
    test_input_validation()
    test_large_dp_vs_known_bound()
    test_randomized_differential()
    print("\n全部测试通过 ✔")
