"""暴力参考实现（对拍基准）。

dom_brute: 对每个可达块 v，支配者 = 删除该点后使 v 从入口不可达的点 u
（含 u == v 自身）。O(n * (n + e))，只用于小规模对拍。
df_brute: 直接用定义 DF(x) = { y | x 支配 y 的某前驱 且 x 不严格支配 y }。
"""

from __future__ import annotations

from typing import Dict, Set

from .cfg import CFG


def _reachable_without(cfg: CFG, removed: int, universe: Set[int]) -> Set[int]:
    seen: Set[int] = set()
    if cfg.entry == removed:
        return seen
    stack = [cfg.entry]
    while stack:
        u = stack.pop()
        if u in seen or u == removed:
            continue
        seen.add(u)
        for v in cfg.blocks[u].succs:
            if v in universe and v not in seen and v != removed:
                stack.append(v)
    return seen


def dom_brute(cfg: CFG) -> Dict[int, Set[int]]:
    universe = set(cfg.reachable_ids)
    dom: Dict[int, Set[int]] = {}
    for v in universe:
        dom[v] = set()
        for u in universe:
            if v not in _reachable_without(cfg, u, universe):
                dom[v].add(u)
    return dom


def df_brute(cfg: CFG, dom: Dict[int, Set[int]]) -> Dict[int, Set[int]]:
    universe = set(cfg.reachable_ids)
    df: Dict[int, Set[int]] = {x: set() for x in universe}
    for x in universe:
        for y in universe:
            preds = [p for p in cfg.blocks[y].preds if p in universe]
            if any(x in dom[p] for p in preds):
                # x 支配 y 的某前驱；检查 x 不严格支配 y
                strictly = x in dom[y] and x != y
                if not strictly:
                    df[x].add(y)
    return df
