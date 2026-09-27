"""Optimal instruction selection by tree covering over expression DAGs.

Model
-----
* The IR is a DAG of `Node`s (operator + children).  Reusing the same node
  object as the child of several parents denotes a *shared subexpression*.
* An instruction is described by one or more pattern trees (`Pat`).  A
  pattern matches an IR fragment; pattern leaves are value non-terminals
  (``Pat.any()`` / ``Pat.V(i)``) whose values must be produced by other
  instructions.  Internal pattern nodes are consumed by the instruction
  itself (e.g. an immediate operand), so they generate no value.
* Shared nodes (refcount > 1) and roots are *materialized*: each is
  computed exactly once by exactly one instruction, its cost is counted
  exactly once, and all uses refer to that value.  Consequently a pattern
  may never span across a materialized node except at the match root:
  shared nodes always occur at pattern leaves.  Shared subexpressions are
  never recomputed (this is the explicit sharing policy).
* A multi-output instruction (`Instr` with several patterns, e.g.
  ``divmod``) covers several materialized nodes at once.  ``Pat.V(i)``
  leaves with the same index must bind to the same node in every pattern
  of the instruction.  Restriction (documented): every output node of a
  multi-output instruction must be materialized (a root or a shared node).
* Selection is exact: bottom-up DP per materialized region plus exhaustive
  enumeration of compatible multi-output matches (a greedy fallback kicks
  in only if there are more than `max_exact_matches` matches).
"""

from collections import namedtuple

INF = float("inf")


class Node:
    """IR expression node.  Identity based: reuse the object to share it."""

    _next_id = 0
    __slots__ = ("op", "children", "val", "id")

    def __init__(self, op, children=(), val=None):
        self.op = op
        self.children = tuple(children)
        self.val = val
        self.id = Node._next_id
        Node._next_id += 1

    def __repr__(self):
        if self.children:
            return "%s(%s)#%d" % (
                self.op, ", ".join(repr(c) for c in self.children), self.id)
        if self.val is not None:
            return "%s(%r)#%d" % (self.op, self.val, self.id)
        return "%s#%d" % (self.op, self.id)


class Pat:
    """Pattern tree.

    Pat("add", (x, y))  internal node, consumed by the instruction
    Pat.any()           value non-terminal (operand produced elsewhere)
    Pat.V(i)            value non-terminal; equal i bind to the same node
    pred                optional extra constraint on the matched node
    """

    __slots__ = ("op", "children", "var", "pred")

    def __init__(self, op=None, children=(), var=None, pred=None):
        self.op = op
        self.children = tuple(children)
        self.var = var
        self.pred = pred

    @staticmethod
    def any():
        return Pat()

    @staticmethod
    def V(i):
        return Pat(var=i)


class Instr:
    """An instruction: name, cost, one pattern (single output) or several."""

    def __init__(self, name, cost, patterns):
        if isinstance(patterns, Pat):
            patterns = [patterns]
        self.name = name
        self.cost = cost
        self.patterns = list(patterns)
        for p in self.patterns:
            if p.op is None:
                raise ValueError(
                    "pattern root of %r must be a concrete operator" % name)

    @property
    def n_outputs(self):
        return len(self.patterns)

    def __repr__(self):
        return "Instr(%s, cost=%s, outputs=%d)" % (
            self.name, self.cost, self.n_outputs)


class UncoverableError(Exception):
    """Raised when some required node cannot be covered by any pattern."""


def _match(pat, node, materialized, bindings, leaves, is_root=True):
    """Try to match `pat` at `node`.

    Binds named leaves (`Pat.V`) into `bindings` and appends every matched
    value leaf to `leaves` (in traversal order).  Internal matches on
    materialized nodes other than the match root are rejected so patterns
    never span a shared node.
    """
    if pat.op is None:
        if pat.pred is not None and not pat.pred(node):
            return False
        if pat.var is not None:
            prev = bindings.get(pat.var)
            if prev is not None:
                if prev is not node:
                    return False
            else:
                bindings[pat.var] = node
        leaves.append(node)
        return True
    if pat.op != node.op or len(pat.children) != len(node.children):
        return False
    if pat.pred is not None and not pat.pred(node):
        return False
    if not is_root and node in materialized:
        return False
    for pc, nc in zip(pat.children, node.children):
        if not _match(pc, nc, materialized, bindings, leaves, False):
            return False
    return True


def _refcounts(roots):
    """Number of incoming edges for each node (root occurrences included)."""
    counts = {}
    for r in roots:
        counts[r] = counts.get(r, 0) + 1
    visited = set()
    stack = list(roots)
    while stack:
        n = stack.pop()
        if n in visited:
            continue
        visited.add(n)
        for c in n.children:
            counts[c] = counts.get(c, 0) + 1
            stack.append(c)
    return counts


def _topo(roots):
    """Post-order of all nodes reachable from roots (iterative, DAG safe)."""
    order = []
    seen = set()
    for r in roots:
        stack = [(r, False)]
        while stack:
            n, done = stack.pop()
            if done:
                order.append(n)
                continue
            if n in seen:
                continue
            seen.add(n)
            stack.append((n, True))
            for c in n.children:
                if c not in seen:
                    stack.append((c, False))
    return order


_Match = namedtuple("_Match", "instr outputs leaves cost")


class Step(namedtuple("Step", "instr results operands cost")):
    __slots__ = ()

    def __repr__(self):
        res = ", ".join("v%d" % i for i in self.results)
        ops = ", ".join("v%d" % i for i in self.operands)
        return "%-7s %-9s <- %s (cost %s)" % (
            self.instr, res, ops, self.cost)


class Selection:
    def __init__(self, steps, total_cost):
        self.steps = steps
        self.total_cost = total_cost

    def __len__(self):
        return len(self.steps)

    def __repr__(self):
        body = "\n".join("  " + repr(s) for s in self.steps)
        return "Selection(total_cost=%s, %d instructions):\n%s" % (
            self.total_cost, len(self.steps), body)


def _has_cycle(matches):
    """Cycle among multi-output matches: edge i->j if a leaf of i is output j."""
    owner = {}
    for i, mt in enumerate(matches):
        for o in mt.outputs:
            owner[o] = i
    adj = [[] for _ in matches]
    for i, mt in enumerate(matches):
        for leaf in mt.leaves:
            j = owner.get(leaf)
            if j is not None and j != i:
                adj[i].append(j)
    color = [0] * len(matches)

    def dfs(i):
        color[i] = 1
        for j in adj[i]:
            if color[j] == 1 or (color[j] == 0 and dfs(j)):
                return True
        color[i] = 2
        return False

    return any(color[i] == 0 and dfs(i) for i in range(len(matches)))


def select(roots, instrs, max_exact_matches=24):
    """Select a minimum-cost instruction cover of the expression DAG.

    Returns a `Selection` (ordered instruction list + total cost).
    Raises `UncoverableError` (with a root-to-node path) if coverage fails.
    """
    if isinstance(roots, Node):
        roots = [roots]
    roots = list(roots)
    if not roots:
        raise ValueError("select() requires at least one root")

    counts = _refcounts(roots)
    materialized = {n for n, c in counts.items() if c > 1}
    materialized.update(roots)
    topo = _topo(roots)

    single = [i for i in instrs if i.n_outputs == 1]
    multi = [i for i in instrs if i.n_outputs > 1]

    # --- bottom-up DP: gcost[n] = min cost to make node n a value ---------
    gcost, choice = {}, {}
    for n in topo:
        best, best_choice = INF, None
        for ins in single:
            bindings, leaves = {}, []
            if not _match(ins.patterns[0], n, materialized, bindings, leaves):
                continue
            c = ins.cost
            for leaf in leaves:
                lc = 0 if leaf in materialized else gcost[leaf]
                if lc == INF:
                    c = INF
                    break
                c += lc
            if c < best:
                best, best_choice = c, (ins, leaves)
        gcost[n], choice[n] = best, best_choice

    # --- find all multi-output matches between materialized nodes ---------
    by_op = {}
    for m in materialized:
        by_op.setdefault(m.op, []).append(m)

    matches = []
    for ins in multi:
        def backtrack(k, used, bindings, leaves, outs):
            if k == ins.n_outputs:
                c = ins.cost
                for leaf in leaves:
                    lc = 0 if leaf in materialized else gcost[leaf]
                    if lc == INF:
                        return
                    c += lc
                matches.append(_Match(ins, tuple(outs), tuple(leaves), c))
                return
            p = ins.patterns[k]
            for cand in by_op.get(p.op, ()):
                if cand in used:
                    continue
                b2, l2 = dict(bindings), []
                if _match(p, cand, materialized, b2, l2):
                    backtrack(k + 1, used | {cand}, b2,
                              leaves + l2, outs + [cand])
        backtrack(0, set(), {}, [], [])

    covered_by_match = set()
    for mt in matches:
        covered_by_match.update(mt.outputs)

    # --- error reporting: deepest hopeless node + path from a root --------
    def hopeless(n):
        return gcost[n] == INF and n not in covered_by_match

    if any(hopeless(m) for m in materialized):
        cause = None
        for n in topo:  # post-order => deepest candidates come first
            if hopeless(n) and all(not hopeless(c) for c in n.children):
                cause = n
                break
        if cause is None:
            cause = next(m for m in materialized if hopeless(m))
        parent = {}
        for n in topo:
            for c in n.children:
                parent.setdefault(c, n)
        chain = [cause]
        while chain[-1] in parent:
            chain.append(parent[chain[-1]])
        chain.reverse()
        path = " -> ".join("%s#%d" % (x.op, x.id) for x in chain)
        raise UncoverableError(
            "no instruction pattern covers node %s#%d; path from root: %s"
            % (cause.op, cause.id, path))

    return _finish(roots, instrs, materialized, gcost, choice, matches,
                   max_exact_matches)


def _finish(roots, instrs, materialized, gcost, choice, matches,
            max_exact_matches):
    """Pick the best compatible set of multi-output matches and emit code."""
    must_cover = {m for m in materialized if gcost[m] == INF}
    base = sum(g for g in (gcost[m] for m in materialized) if g < INF)

    def savings(mt):
        return sum(gcost[o] for o in mt.outputs if gcost[o] < INF) - mt.cost

    # Matches that lose money can never enter an optimum, except matches
    # needed to cover nodes that have no single-output cover.
    forced = [mt for mt in matches
              if any(o in must_cover for o in mt.outputs)]
    useful = [mt for mt in matches if savings(mt) > 0] or forced
    if not useful:
        useful = matches
    matches = sorted(set(forced) | set(useful), key=lambda mt: -savings(mt))

    best_total, best_sel = INF, None

    def consider(sel, covered):
        nonlocal best_total, best_sel
        if not must_cover <= covered:
            return
        total = base
        for mt in sel:
            total -= sum(gcost[o] for o in mt.outputs if gcost[o] < INF)
            total += mt.cost
        if total < best_total and not _has_cycle(sel):
            best_total, best_sel = total, list(sel)

    if len(matches) <= max_exact_matches:
        suffix = [0] * (len(matches) + 1)
        for i in range(len(matches) - 1, -1, -1):
            s = savings(matches[i])
            suffix[i] = suffix[i + 1] + (s if s > 0 else 0)

        def search(i, used, sel, covered, cur_total):
            nonlocal best_total, best_sel
            if cur_total - suffix[i] >= best_total:
                return  # remaining positive savings cannot beat best
            if i == len(matches):
                if must_cover <= covered and not _has_cycle(sel) \
                        and cur_total < best_total:
                    best_total, best_sel = cur_total, list(sel)
                return
            search(i + 1, used, sel, covered, cur_total)  # exclude
            mt = matches[i]
            if not any(o in used for o in mt.outputs):
                sel.append(mt)
                search(i + 1, used | set(mt.outputs), sel,
                       covered | set(mt.outputs), cur_total - savings(mt))
                sel.pop()

        search(0, set(), [], set(), base)
    else:
        # Greedy fallback (only for very large numbers of multi-output
        # matches; exact DP is exponential in their count).
        sel, covered = [], set()
        for mt in matches:
            if any(o in covered for o in mt.outputs):
                continue
            if savings(mt) > 0 or any(o in must_cover for o in mt.outputs):
                sel.append(mt)
                covered.update(mt.outputs)
        consider(sel, covered)

    if best_sel is None:
        raise UncoverableError(
            "required nodes can only be produced by conflicting "
            "multi-output instructions")

    return _emit(roots, materialized, choice, best_sel, best_total)


def _emit(roots, materialized, choice, chosen, total_cost):
    """Emit instructions in dependency order (iterative post-order)."""
    steps = []
    emitted = set()
    match_of = {}
    for mt in chosen:
        for o in mt.outputs:
            match_of[o] = mt
    done_matches = set()

    def operands_of(n):
        mt = match_of.get(n)
        return mt.leaves if mt is not None else choice[n][1]

    def emit_now(n):
        mt = match_of.get(n)
        if mt is not None:
            key = id(mt)
            if key in done_matches:
                return
            done_matches.add(key)
            emitted.update(mt.outputs)
            steps.append(Step(mt.instr.name,
                              tuple(o.id for o in mt.outputs),
                              tuple(l.id for l in mt.leaves),
                              mt.instr.cost))
        else:
            if n in emitted:
                return
            emitted.add(n)
            ins, leaves = choice[n]
            steps.append(Step(ins.name, (n.id,),
                              tuple(l.id for l in leaves), ins.cost))

    for r in roots:
        stack = [(r, False)]
        while stack:
            n, done = stack.pop()
            if n in emitted:
                continue
            if not done:
                stack.append((n, True))
                for leaf in operands_of(n):
                    if leaf not in emitted:
                        stack.append((leaf, False))
            else:
                emit_now(n)

    assert sum(s.cost for s in steps) == total_cost
    return Selection(steps, total_cost)
