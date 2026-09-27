"""Exhaustive (exponential) instruction selection, for cross-checking.

Independently enumerates *every* covering of every materialized region and
*every* compatible subset of multi-output matches, then takes the minimum
total cost.  Only usable on small DAGs.
"""

from itertools import product

from iselect import INF, _match, _refcounts


def _cycle(sel):
    owner = {}
    for i, (_, outs, _, _) in enumerate(sel):
        for o in outs:
            owner[o] = i
    adj = [[] for _ in sel]
    for i, (_, _, leaves, _) in enumerate(sel):
        for leaf in leaves:
            j = owner.get(leaf)
            if j is not None and j != i:
                adj[i].append(j)
    color = [0] * len(sel)

    def dfs(i):
        color[i] = 1
        for j in adj[i]:
            if color[j] == 1 or (color[j] == 0 and dfs(j)):
                return True
        color[i] = 2
        return False

    return any(color[i] == 0 and dfs(i) for i in range(len(sel)))


def brute_force(roots, instrs):
    """Return the exact minimum total cost (INF if uncoverable)."""
    if not isinstance(roots, list):
        roots = [roots]
    counts = _refcounts(roots)
    materialized = {n for n, c in counts.items() if c > 1}
    materialized.update(roots)
    single = [i for i in instrs if i.n_outputs == 1]
    multi = [i for i in instrs if i.n_outputs > 1]

    def cover_costs(n):
        """Cost of EVERY possible cover of n as an independent value."""
        out = []
        for ins in single:
            bindings, leaves = {}, []
            if not _match(ins.patterns[0], n, materialized, bindings, leaves):
                continue
            per_leaf = [[0] if leaf in materialized else cover_costs(leaf)
                        for leaf in leaves]
            if not all(per_leaf):
                continue
            for combo in product(*per_leaf):
                out.append(ins.cost + sum(combo))
        return out

    def min_cover(n):
        costs = cover_costs(n)
        return min(costs) if costs else INF

    # every multi-output match, found by an independent backtracking search
    matches = []
    for ins in multi:
        def backtrack(k, used, bindings, leaves, outs):
            if k == ins.n_outputs:
                c = ins.cost
                for leaf in leaves:
                    lc = 0 if leaf in materialized else min_cover(leaf)
                    if lc == INF:
                        return
                    c += lc
                matches.append((ins, tuple(outs), tuple(leaves), c))
                return
            p = ins.patterns[k]
            for cand in materialized:
                if cand in used or cand.op != p.op:
                    continue
                b2, l2 = dict(bindings), []
                if _match(p, cand, materialized, b2, l2):
                    backtrack(k + 1, used | {cand}, b2,
                              leaves + l2, outs + [cand])
        backtrack(0, set(), {}, [], [])

    best = INF
    for mask in range(1 << len(matches)):
        sel = [matches[i] for i in range(len(matches)) if (mask >> i) & 1]
        used = set()
        ok = True
        for _, outs, _, _ in sel:
            if used & set(outs):
                ok = False
                break
            used.update(outs)
        if not ok or _cycle(sel):
            continue
        total = sum(c for _, _, _, c in sel)
        for m in materialized:
            if m in used:
                continue
            c = min_cover(m)
            if c == INF:
                total = INF
                break
            total += c
        best = min(best, total)
    return best
