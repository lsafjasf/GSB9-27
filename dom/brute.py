"""Reference dominator computation by brute-force reachability.

d dominates n  <=>  removing d from the graph makes n unreachable
from the entry (plus d == n trivially).  O(V * (V + E)); used only to
cross-check the fast algorithm on small random graphs.
"""

from .cfg import CFG


def _reachable_without(cfg, banned):
    seen = set()
    stack = [cfg.entry]
    while stack:
        name = stack.pop()
        if name == banned or name in seen or name not in cfg.blocks:
            continue
        seen.add(name)
        stack.extend(cfg.blocks[name].succs)
    return seen


def brute_dominator_sets(cfg):
    reachable = cfg.reachable_names()
    doms = {}
    for n in reachable:
        ds = {n}
        for d in reachable:
            if d == n:
                continue
            if n not in _reachable_without(cfg, d):
                ds.add(d)
        doms[n] = ds
    return doms


def brute_idom(cfg):
    """idom(n) = the dominator of n (other than n) dominated by all
    other strict dominators of n."""
    doms = brute_dominator_sets(cfg)
    idom = {}
    for n, ds in doms.items():
        strict = ds - {n}
        if not strict:
            idom[n] = n            # entry
            continue
        # idom(n) is the strict dominator dominated by every other
        # strict dominator of n (i.e. the one closest to n).
        for cand in strict:
            if all(other in doms[cand] or other == cand
                   for other in strict):
                idom[n] = cand
                break
    return idom


def brute_dominance_frontiers(cfg, doms=None):
    """Reference DF derived from the definition:

    b in DF[a]  <=>  a dominates some (reachable) predecessor of b
    and a does not strictly dominate b.
    """
    if doms is None:
        doms = brute_dominator_sets(cfg)
    df = {a: set() for a in doms}
    for b in doms:
        for pred in cfg.blocks[b].preds:
            if pred not in doms:
                continue
            for a in doms[pred]:
                if a == b or a not in doms[b]:
                    df[a].add(b)
    return df
