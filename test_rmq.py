"""对拍自测：SparseTableRMQ vs 暴力扫描，逐次比对 (值, 下标)。

运行：python3 test_rmq.py
"""

import random

from rmq import SparseTableRMQ


def brute_min(data, l, r):
    best = l
    for i in range(l + 1, r + 1):
        if data[i] < data[best]:  # 严格小于才更新 => 平局取最左
            best = i
    return data[best], best


def brute_max(data, l, r):
    best = l
    for i in range(l + 1, r + 1):
        if data[i] > data[best]:  # 严格大于才更新 => 平局取最左
            best = i
    return data[best], best


def check_array(data, tag):
    st = SparseTableRMQ(data)
    n = len(data)
    pairs = [(l, r) for l in range(n) for r in range(l, n)] if n <= 40 else []
    if not pairs:  # 大数组随机抽区间
        rng = random.Random(1234)
        for _ in range(3000):
            l = rng.randrange(n)
            pairs.append((l, l + rng.randrange(n - l)))
    for l, r in pairs:
        got_min = st.range_min(l, r)
        got_max = st.range_max(l, r)
        exp_min = brute_min(data, l, r)
        exp_max = brute_max(data, l, r)
        assert got_min == exp_min, f"[{tag}] min [{l},{r}]: got {got_min}, want {exp_min}"
        assert got_max == exp_max, f"[{tag}] max [{l},{r}]: got {got_max}, want {exp_max}"


def test_edge_cases():
    check_array([42], "单元素")
    check_array([7] * 50, "全相同")
    check_array([5, 5, 5, 1, 5, 1, 5], "重复最值")
    check_array(list(range(60)), "严格递增")
    check_array(list(range(60, 0, -1)), "严格递减")
    check_array([-3, -1, -3, -1, -3], "负数重复")
    check_array([0, 10**18, -(10**18), 0], "极大极小值")

    st = SparseTableRMQ([9, 4, 4, 7, 1])
    assert st.range_min(0, 0) == (9, 0)          # 区间长度 1
    assert st.range_max(3, 3) == (7, 3)          # 区间长度 1
    assert st.range_min(0, 4) == (1, 4)          # 覆盖全数组
    assert st.range_max(0, 4) == (9, 0)          # 覆盖全数组
    assert st.range_min(1, 2) == (4, 1)          # 平局取最左
    assert st.range_max(1, 2) == (4, 1)

    try:
        SparseTableRMQ([])
    except ValueError:
        pass
    else:
        raise AssertionError("空数组应抛 ValueError")

    try:
        st.range_min(2, 1)
    except IndexError:
        pass
    else:
        raise AssertionError("非法区间应抛 IndexError")
    print("边界用例全部通过")


def test_random_fuzz(rounds=300, seed=20260927):
    rng = random.Random(seed)
    for t in range(rounds):
        n = rng.randint(1, 120)
        # 小值域制造大量平局；大值域覆盖一般情形
        lo, hi = (0, 3) if t % 2 == 0 else (-10**9, 10**9)
        data = [rng.randint(lo, hi) for _ in range(n)]
        check_array(data, f"random#{t} n={n}")
    print(f"随机对拍 {rounds} 轮全部通过")


if __name__ == "__main__":
    test_edge_cases()
    test_random_fuzz()
    print("OK: 所有测试通过")
