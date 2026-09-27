"""最小使用示例：python3 demo.py"""

from kth import (
    KthError,
    kth_combination,
    kth_multiset,
    kth_permutation,
    multiset_combination_count,
    multiset_permutation_count,
)

elements = [1, 1, 2, 3]

n_perm = multiset_permutation_count([2, 1, 1])
print("排列总数:", n_perm)
print("第 1 个排列:", kth_permutation(elements, 1))
print("最后一个排列:", kth_permutation(elements, n_perm))

n_comb = multiset_combination_count([2, 1, 1], r=2)
print("长度为 2 的组合总数:", n_comb)
print("第 1 个组合:", kth_combination(elements, 2, 1))
print("最后一个组合:", kth_combination(elements, 2, n_comb))

print("统一入口-排列:", kth_multiset(elements, 3, kind="permutation"))
print("统一入口-组合:", kth_multiset(elements, 2, kind="combination", r=2))

try:
    kth_permutation(elements, n_perm + 1)
except KthError as exc:
    print("越界报错:", exc)
