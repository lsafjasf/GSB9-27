"""Dominator analysis on a CFG.

Implements the iterative dataflow algorithm from
  Cooper, Harvey, Kennedy: "A Simple, Fast Dominance Algorithm"
and Cytron-style dominance frontiers.

Only blocks reachable from the entry take part in the analysis; callers
should list unreachable blocks separately (see CFG.unreachable_names()).
"""


def reverse_postorder(cfg):
    """RPO of the reachable subgraph; also returns postorder numbers."""
    reachable = cfg.reachable_names()
    post = []
    state = {}                     # name -> 1 (on stack) / 2 (done)
    # iterative DFS to avoid recursion limits on deep graphs
    stack = [(cfg.entry, iter(cfg.blocks[cfg.entry].succs))]
    state[cfg.entry] = 1
    while stack:
        name, it = stack[-1]
        advanced = False
        for succ in it:
            if succ in reachable and state.get(succ) is None:
                state[succ] = 1
                stack.append((succ, iter(cfg.blocks[succ].succs)))
                advanced = True
                break
        if not advanced:
            state[name] = 2
            post.append(name)
            stack.pop()
    po_num = {name: i for i, name in enumerate(post)}
    return list(reversed(post)), po_num


def _intersect(finger1, finger2, po_num, idom):
    while finger1 != finger2:
        while po_num[finger1] < po_num[finger2]:
            finger1 = idom[finger1]
        while po_num[finger2] < po_num[finger1]:
            finger2 = idom[finger2]
    return finger1


def compute_idom(cfg):
    """Immediate dominators for every reachable block.

    Returns dict name -> idom name; idom[entry] == entry.
    """
    rpo, po_num = reverse_postorder(cfg)
    entry = cfg.entry
    idom = {entry: entry}
    changed = True
    while changed:
        changed = False
        for name in rpo:
            if name == entry:
                continue
            # only reachable predecessors can have an idom already
            new_idom = None
            for pred in cfg.blocks[name].preds:
                if pred not in idom:
                    continue
                if new_idom is None:
                    new_idom = pred
                else:
                    new_idom = _intersect(pred, new_idom, po_num, idom)
            if new_idom is not None and idom.get(name) != new_idom:
                idom[name] = new_idom
                changed = True
    return idom


def dominator_sets(idom):
    """Full dominator set of each block, derived from the idom chain."""
    doms = {}
    for name in idom:
        chain = set()
        cur = name
        while cur not in chain:
            chain.add(cur)
            cur = idom[cur]
        doms[name] = chain
    return doms


def dominator_tree(idom):
    """Children map of the dominator tree: name -> [child names]."""
    children = {name: [] for name in idom}
    for name, parent in idom.items():
        if name != parent:
            children[parent].append(name)
    return children


def dominance_frontiers(cfg, idom):
    """Cytron's algorithm: DF[name] = set of frontier block names."""
    df = {name: set() for name in idom}
    for name in idom:
        preds = [p for p in cfg.blocks[name].preds if p in idom]
        # The classic ">= 2 preds" filter must not skip the entry block:
        # with a self loop or a back edge to the entry, the entry is its
        # own frontier (loop headers need phi nodes there too).
        if len(preds) < 2 and name != cfg.entry:
            continue
        for pred in preds:
            runner = pred
            while True:
                if runner == idom[name] and runner != name:
                    break
                df[runner].add(name)
                if runner == name:
                    # Reached the frontier block itself.  For the entry
                    # (idom[entry] == entry) this is exactly the case the
                    # usual "walk up to idom[name]" loop misses.
                    break
                runner = idom[runner]
    return df


def analyze(cfg):
    """Convenience wrapper: returns a dict with all analysis results."""
    idom = compute_idom(cfg)
    return {
        "unreachable": cfg.unreachable_names(),
        "idom": idom,
        "doms": dominator_sets(idom),
        "dom_tree": dominator_tree(idom),
        "df": dominance_frontiers(cfg, idom),
    }
