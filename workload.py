"""人为构造的调用序列：结构固定、CPU 密集，用于对拍与开销测量。

调用结构（每次 repeat）：
    run_sequence
      ├── task_a ── leaf_a          (约 5/10 的 CPU)
      ├── task_b ── leaf_b1 + leaf_b2 (约 3/10，两个并列子调用)
      └── task_c ── mid_c ── leaf_c   (约 2/10，多一层中间帧)
"""

from __future__ import annotations


def _spin(iters: int, seed: int) -> int:
    acc = 0
    for i in range(iters):
        acc += (i * seed) % 1000003
    return acc


def leaf_a(iters: int) -> int:
    return _spin(iters, 2654435761)


def leaf_b1(iters: int) -> int:
    return _spin(iters, 40503)


def leaf_b2(iters: int) -> int:
    return _spin(iters, 83492791)


def leaf_c(iters: int) -> int:
    return _spin(iters, 2246822519)


def mid_c(iters: int) -> int:
    return leaf_c(iters)


def task_a(iters: int) -> int:
    return leaf_a(iters)


def task_b(iters: int) -> int:
    half = iters // 2
    return leaf_b1(half) + leaf_b2(iters - half)


def task_c(iters: int) -> int:
    return mid_c(iters)


def run_sequence(unit: int, repeats: int = 1) -> int:
    total = 0
    for _ in range(repeats):
        total += task_a(5 * unit)
        total += task_b(3 * unit)
        total += task_c(2 * unit)
    return total


# 需要纳入统计/对比的函数名（叶子 + 中间层 + 忙等叶子 _spin）
TRACED_FUNCS = [
    "_spin",
    "leaf_a", "leaf_b1", "leaf_b2", "leaf_c",
    "mid_c", "task_a", "task_b", "task_c",
]
