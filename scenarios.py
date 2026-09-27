"""四类边界场景：极短调用 / 单层调用 / 超深栈 / 采样频率高于调用频率。

直接运行打印数据；selftest.py 复用其中的场景做断言。
"""

from __future__ import annotations

import sys
import time

from sampler import FrameKey, StackSampler, StackTree, fold_frames

# 各场景 CPU 运行时长（秒）
DURATION = 0.5


def _spin_ms(ms: float) -> None:
    """纯 CPU 忙等约 ms 毫秒。"""
    end = time.process_time() + ms / 1000.0
    acc = 0
    while time.process_time() < end:
        acc += 1


# ---------- 1. 极短调用：绝大多数调用比采样间隔短，采样大量丢失调用 ----------

def _tiny():
    return 1


def scenario_short_calls(interval_ms: float = 1.0) -> dict:
    sampler = StackSampler(interval=interval_ms / 1000.0)
    calls = 0
    deadline = time.process_time() + DURATION
    sampler.start()
    while time.process_time() < deadline:
        _tiny()
        calls += 1
    sampler.stop()

    stats = sampler.tree.func_stats()
    tiny_total = stats.get("_tiny", (0, 0))[1]
    n = sampler.tree.samples or 1
    return {
        "name": "极短调用",
        "tiny_calls": calls,
        "samples": sampler.tree.samples,
        "tiny_hit_samples": tiny_total,
        "tiny_hit_pct": 100.0 * tiny_total / n,
        "loss": sampler.loss_report(),
    }


# ---------- 2. 单层调用：只有 driver -> leaf 一层 ----------

def _leaf_one(ms: float):
    _spin_ms(ms)


def _driver_one(total_ms: float):
    n = 10
    for _ in range(n):
        _leaf_one(total_ms / n)


def scenario_single_level(interval_ms: float = 1.0) -> dict:
    sampler = StackSampler(interval=interval_ms / 1000.0)
    sampler.start()
    _driver_one(DURATION * 1000)
    sampler.stop()

    # 从 _driver_one 节点向下取占比最高的主路径，应为 _leaf_one -> _spin_ms
    driver = next(nd for nd in sampler.tree.iter_nodes()
                  if nd.key.func == "_driver_one")
    chain = []
    node = driver
    while node.children:
        node = max(node.children.values(), key=lambda c: c.total_count)
        chain.append(node.key.func)
    return {
        "name": "单层调用",
        "samples": sampler.tree.samples,
        "chain_below_driver": chain,
        "nodes": {k.key.func: (k.self_count, k.total_count)
                  for k in sampler.tree.iter_nodes() if k is not sampler.tree.root},
        "loss": sampler.loss_report(),
    }


# ---------- 3. 超深栈 ----------

def scenario_deep_stack(interval_ms: float = 1.0, depth: int = 900) -> dict:
    # 3a. 纯数据层面：100k 深度的直接递归栈折叠后应为 2 个节点
    huge = [FrameKey("rec", "synthetic", 1)] * 100_000
    huge.append(FrameKey("bottom", "synthetic", 2))
    folded = fold_frames(huge)

    # 3b. 真实深度 depth 的递归，采样器在递归栈底采样，整段时间栈都极深
    sys.setrecursionlimit(max(2000, depth + 500))

    def rec(n):
        if n == 0:
            end = time.process_time() + DURATION
            while time.process_time() < end:
                pass
            return
        return rec(n - 1)

    sampler = StackSampler(interval=interval_ms / 1000.0)
    sampler.start()
    rec(depth)
    sampler.stop()

    rec_nodes = [nd for nd in sampler.tree.iter_nodes() if nd.key.func == "rec"]
    # rec 节点的父节点函数名（应直接是场景函数，证明 900 层递归折叠为 1 个节点）
    parent_of = {}
    for nd in sampler.tree.iter_nodes():
        for ch in nd.children.values():
            parent_of[id(ch)] = nd.key.func
    rec_parent = parent_of.get(id(rec_nodes[0])) if rec_nodes else None
    return {
        "name": "超深栈",
        "synthetic_depth": 100_001,
        "synthetic_folded_len": len(folded),
        "live_recursion_depth": depth,
        "samples": sampler.tree.samples,
        "rec_node_count": len(rec_nodes),       # 必须 == 1
        "rec_parent_func": rec_parent,
        "rec_total_hits": rec_nodes[0].total_count if rec_nodes else 0,
        "loss": sampler.loss_report(),
    }


# ---------- 4. 采样频率高于调用频率：每次调用被重复采样，节点不得重复 ----------

def _heavy_call():
    _spin_ms(5.0)  # 每次调用约 5ms CPU


def scenario_fast_sampling(interval_ms: float = 0.2) -> dict:
    n_calls = int(DURATION / 0.005)
    sampler = StackSampler(interval=interval_ms / 1000.0)
    sampler.start()
    for _ in range(n_calls):
        _heavy_call()
    sampler.stop()

    # 检查树上不存在“父子同名”（递归折叠性质）
    duplicate_edges = 0
    for node in sampler.tree.iter_nodes():
        for ch in node.children.values():
            if ch.key.func == node.key.func:
                duplicate_edges += 1
    heavy = sampler.tree.func_stats().get("_heavy_call", (0, 0))
    n = sampler.tree.samples or 1
    return {
        "name": "采样频率高于调用频率",
        "interval_ms": interval_ms,
        "call_duration_ms": 5.0,
        "samples_per_call": 5.0 / interval_ms,
        "calls": n_calls,
        "samples": sampler.tree.samples,
        "heavy_node_count": sum(1 for nd in sampler.tree.iter_nodes()
                                if nd.key.func == "_heavy_call"),
        "heavy_total_pct": 100.0 * heavy[1] / n,
        "duplicate_parent_child_edges": duplicate_edges,
        "loss": sampler.loss_report(),
    }


SCENARIOS = [
    scenario_short_calls,
    scenario_single_level,
    scenario_deep_stack,
    scenario_fast_sampling,
]


def main() -> None:
    for fn in SCENARIOS:
        print(f"===== {fn.__name__} =====")
        result = fn()
        for k, v in result.items():
            print(f"  {k}: {v}")
        print()


if __name__ == "__main__":
    main()
