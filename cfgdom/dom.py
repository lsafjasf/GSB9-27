"""支配关系分析：支配集、立即支配（支配树）、支配边界。

只覆盖从入口可达的块；不可达块由 CFG.unreachable 单独列出，
不参与任何支配计算。支持多出口与自环。
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from .cfg import CFG


def _rpo(cfg: CFG) -> List[int]:
    """逆后序（reverse postorder），仅含可达块。"""
    order: List[int] = []
    visited: Set[int] = set()
    # 迭代 DFS，避免深图递归溢出
    stack: List[Tuple[int, int]] = [(cfg.entry, 0)]
    visited.add(cfg.entry)
    while stack:
        u, ci = stack[-1]
        succs = cfg.blocks[u].succs
        if ci < len(succs):
            stack[-1] = (u, ci + 1)
            v = succs[ci]
            if v not in visited:
                visited.add(v)
                stack.append((v, 0))
        else:
            order.append(u)
            stack.pop()
    order.reverse()
    return order


def dominators(cfg: CFG) -> Dict[int, Set[int]]:
    """迭代数据流算法求支配集：Dom(n) = {n} ∪ ⋂ Dom(p), p ∈ preds(n)。

    返回 {块号: 支配集}，仅含可达块。
    """
    rpo = _rpo(cfg)
    reach = set(rpo)
    dom: Dict[int, Set[int]] = {n: set(reach) for n in reach}
    dom[cfg.entry] = {cfg.entry}
    changed = True
    while changed:
        changed = False
        for n in rpo:
            if n == cfg.entry:
                continue
            preds = [p for p in cfg.blocks[n].preds if p in reach]
            if preds:
                new = set(dom[preds[0]])
                for p in preds[1:]:
                    new &= dom[p]
            else:
                new = set()
            new.add(n)
            if new != dom[n]:
                dom[n] = new
                changed = True
    return dom


def immediate_dominators(cfg: CFG) -> Dict[int, int]:
    """Cooper-Harvey-Kennedy 算法求 idom（入口的 idom 为其自身）。

    返回 {块号: 立即支配者块号}，仅含可达块。
    """
    rpo = _rpo(cfg)
    rpo_index = {n: i for i, n in enumerate(rpo)}
    idom: Dict[int, int] = {cfg.entry: cfg.entry}

    def intersect(b1: int, b2: int) -> int:
        while b1 != b2:
            while rpo_index[b1] > rpo_index[b2]:
                b1 = idom[b1]
            while rpo_index[b2] > rpo_index[b1]:
                b2 = idom[b2]
        return b1

    changed = True
    while changed:
        changed = False
        for n in rpo:
            if n == cfg.entry:
                continue
            preds = [p for p in cfg.blocks[n].preds if p in rpo_index]
            new_idom = None
            for p in preds:
                if p in idom:
                    new_idom = p if new_idom is None else intersect(p, new_idom)
            if new_idom is not None and idom.get(n) != new_idom:
                idom[n] = new_idom
                changed = True
    return idom


def dominator_tree(cfg: CFG) -> Tuple[Dict[int, int], Dict[int, List[int]]]:
    """返回 (idom, children)：支配树的父指针与子节点表。"""
    idom = immediate_dominators(cfg)
    children: Dict[int, List[int]] = {n: [] for n in idom}
    for n, p in idom.items():
        if n != p:
            children[p].append(n)
    for c in children.values():
        c.sort()
    return idom, children


def dominance_frontier(cfg: CFG) -> Dict[int, Set[int]]:
    """Cytron 等人的算法：基于支配树计算支配边界 DF。

    DF(x) = { y | x 支配 y 的某个前驱，但 x 不严格支配 y }。
    仅含可达块。
    """
    idom, children = dominator_tree(cfg)
    df: Dict[int, Set[int]] = {n: set() for n in idom}
    reach = set(idom)

    # 迭代后序遍历支配树（深链不会爆递归栈）
    order: List[int] = []
    stack: List[int] = [cfg.entry]
    while stack:
        n = stack.pop()
        order.append(n)
        stack.extend(children[n])
    for n in reversed(order):
        for s in cfg.blocks[n].succs:
            # DF-local：n 不严格支配 s（s == n 为自环/入口回边情形）
            if s in reach and (idom.get(s) != n or s == n):
                df[n].add(s)
        for c in children[n]:
            for w in df[c]:
                # DF-up：n 不严格支配 w（w == n 时 n 支配但不严格支配）
                if idom.get(w) != n or w == n:
                    df[n].add(w)
    return df
