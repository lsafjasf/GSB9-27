"""SparseTable 自测：边界情形 + 与暴力扫描随机对拍。

运行：python3 test_rmq.py
"""

import random
from rmq import SparseTable


def brute_min(arr, l, r):
    """暴力：最小值及其最左下标。"""
    best = l
    for i in range(l + 1, r + 1):
        if arr[i] < arr[best]:
            best = i
    return arr[best], best


def brute_max(arr, l, r):
    best = l
    for i in range(l + 1, r + 1):
        if arr[i] > arr[best]:
            best = i
    return arr[best], best


def check_array(arr, tag):
    st = SparseTable(arr)
    n = len(arr)
    for l in range(n):
        for r in range(l, n):
            assert st.range_min(l, r) == brute_min(arr, l, r), (tag, l, r, "min")
            assert st.range_max(l, r) == brute_max(arr, l, r), (tag, l, r, "max")
    print(f"  [OK] {tag}: 全部 {n*(n+1)//2} 个区间逐一对拍一致")


def test_edge_cases():
    print("边界情形：")
    check_array([42], "单元素数组")
    check_array([7] * 50, "全相同元素")
    check_array(list(range(60)), "严格递增")
    check_array(list(range(60, 0, -1)), "严格递减")
    check_array([0, -5, 3, -5, 3, 0, -5], "重复最值交错")

    # 区间长度 1 与覆盖全数组
    arr = [5, 2, 8, 2, 9, 1, 9]
    st = SparseTable(arr)
    for i in range(len(arr)):
        assert st.range_min(i, i) == (arr[i], i)
        assert st.range_max(i, i) == (arr[i], i)
    assert st.range_min(0, len(arr) - 1) == (1, 5)
    assert st.range_max(0, len(arr) - 1) == (9, 4)  # 平局取最左
    print("  [OK] 区间长度 1 / 覆盖全数组 / 平局取最左下标")

    # 非法输入
    for bad in [(-1, 0), (0, 7), (3, 2)]:
        try:
            st.range_min(*bad)
            raise AssertionError("应抛出 IndexError")
        except IndexError:
            pass
    try:
        SparseTable([])
        raise AssertionError("应抛出 ValueError")
    except ValueError:
        pass
    print("  [OK] 空数组与非法区间正确抛错")


def test_fuzz(rounds=300, max_n=200, seed=20260927):
    print(f"随机对拍：{rounds} 轮（含大量重复值的小值域）")
    rng = random.Random(seed)
    for t in range(rounds):
        n = rng.randint(1, max_n)
        # 小值域制造大量平局，检验最左下标规则
        arr = [rng.randint(0, 5) for _ in range(n)]
        st = SparseTable(arr)
        for _ in range(20):
            l = rng.randrange(n)
            r = rng.randrange(l, n)
            assert st.range_min(l, r) == brute_min(arr, l, r), (t, l, r)
            assert st.range_max(l, r) == brute_max(arr, l, r), (t, l, r)
    print(f"  [OK] {rounds} 轮 x 20 区间，min/max 值与下标逐次一致")


if __name__ == "__main__":
    test_edge_cases()
    test_fuzz()
    print("全部测试通过。")
