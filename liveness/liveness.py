"""Liveness analysis (backward dataflow) over a control-flow graph.

Pure standard library, Python 3.8+.

Model
-----
A CFG is a set of basic blocks. Each block has:
  - uses: variables read before being written in the block (upward-exposed uses)
  - defs: variables written in the block
  - succs: successor edges, each tagged with a kind ("normal" or "exceptional")

For liveness, an exceptional edge (e.g. a jump to an exception handler) is
semantically just another control-flow edge: anything live at the handler
entry must be live at the point the exception may be thrown. So all edge
kinds contribute equally to live-out; the kind is kept as metadata so the
benchmarks/fuzzer can build graphs that contain exceptional jumps.

Equations (standard gen/kill, union join, backward):
  live_out[b] = union over s in succ(b) of live_in[s]
  live_in[b]  = uses[b] | (live_out[b] - defs[b])

Two solvers are provided and are meant to agree exactly:
  - analyze():        worklist algorithm, blocks ordered in reverse postorder
                      of the *reversed* CFG (i.e. successors are visited
                      before their predecessors), which is the order that
                      converges fastest for backward problems.
  - analyze_naive():  textbook point-wise iteration: repeated full sweeps in
                      block-id order until nothing changes.

Convergence argument
--------------------
The lattice is the powerset of a finite variable set ordered by inclusion,
hence finite. Both solvers start from the empty set and only ever *add*
variables to live sets (monotone transfer functions, union join), so the
computed values form an ascending chain and must stabilize after finitely
many steps. The worklist invariant is: whenever live_out[b] grows, every
predecessor is (re-)enqueued, so at the fixpoint (empty worklist) every
equation holds for every block -- including blocks unreachable from the
entry, which are seeded into the worklist like any other block.
"""

from collections import deque

NORMAL = "normal"
EXCEPTIONAL = "exceptional"


class Block(object):
    __slots__ = ("id", "uses", "defs", "succs")

    def __init__(self, bid, uses=(), defs=()):
        self.id = bid
        self.uses = frozenset(uses)
        self.defs = frozenset(defs)
        self.succs = []  # list of (target_id, kind)

    def __repr__(self):
        return "Block(%r, uses=%r, defs=%r)" % (self.id, sorted(self.uses), sorted(self.defs))


class CFG(object):
    """A control-flow graph. Blocks may be added in any order; edges may
    reference blocks that do not exist yet (they are materialized as empty
    blocks on demand, which also keeps malformed input total)."""

    def __init__(self):
        self.blocks = {}

    def add_block(self, bid, uses=(), defs=()):
        if bid in self.blocks:
            b = self.blocks[bid]
            b.uses = frozenset(uses)
            b.defs = frozenset(defs)
            return b
        b = Block(bid, uses, defs)
        self.blocks[bid] = b
        return b

    def add_edge(self, src, dst, kind=NORMAL):
        if src not in self.blocks:
            self.add_block(src)
        if dst not in self.blocks:
            self.add_block(dst)
        self.blocks[src].succs.append((dst, kind))

    def predecessors(self):
        preds = {bid: [] for bid in self.blocks}
        for bid, b in self.blocks.items():
            for dst, _kind in b.succs:
                preds[dst].append(bid)
        return preds


def _backward_order(cfg):
    """Reverse postorder of the reversed CFG.

    DFS on predecessor edges, seeded from exit blocks (no successors) first
    and then from any still-unvisited block (covers blocks unreachable from
    the entry and infinite loops with no exit). In the resulting order a
    block generally appears *after* its successors, so backward information
    propagates far on every worklist pass.
    """
    preds = cfg.predecessors()
    visited = set()
    postorder = []

    exits = [bid for bid, b in cfg.blocks.items() if not b.succs]
    roots = exits + [bid for bid in cfg.blocks if bid not in exits]
    for root in roots:
        if root in visited:
            continue
        stack = [(root, iter(preds[root]))]
        visited.add(root)
        while stack:
            bid, it = stack[-1]
            advanced = False
            for p in it:
                if p not in visited:
                    visited.add(p)
                    stack.append((p, iter(preds[p])))
                    advanced = True
                    break
            if not advanced:
                postorder.append(bid)
                stack.pop()
    postorder.reverse()
    return postorder


def analyze(cfg):
    """Worklist solver. Returns (live_in, live_out, stats).

    stats keys:
      rounds   -- number of block (re-)evaluations performed
      sweeps   -- ceil(rounds / n_blocks), a rough comparable to naive sweeps
      order    -- the block visit order used to seed the worklist
    """
    n = len(cfg.blocks)
    live_in = {bid: set() for bid in cfg.blocks}
    live_out = {bid: set() for bid in cfg.blocks}
    if n == 0:
        return live_in, live_out, {"rounds": 0, "sweeps": 0, "order": []}

    preds = cfg.predecessors()
    order = _backward_order(cfg)

    work = deque(order)
    in_work = set(order)
    rounds = 0
    while work:
        bid = work.popleft()
        in_work.discard(bid)
        b = cfg.blocks[bid]
        rounds += 1

        out = set()
        for dst, _kind in b.succs:
            out |= live_in[dst]
        new_in = b.uses | (out - b.defs)

        # Predecessors consume live_in[bid], so they must be re-enqueued
        # exactly when live_in[bid] grew (a self-loop makes this observable:
        # live_out may stay put while live_in changes, or vice versa).
        changed_in = new_in != live_in[bid]
        live_out[bid] = out
        live_in[bid] = new_in
        if changed_in:
            for p in preds[bid]:
                if p not in in_work:
                    work.append(p)
                    in_work.add(p)

    stats = {"rounds": rounds, "sweeps": -(-rounds // n), "order": order}
    return live_in, live_out, stats


def analyze_naive(cfg):
    """Naive point-wise iteration: full sweeps in sorted block-id order
    until a whole sweep changes nothing. Returns (live_in, live_out, stats).

    stats keys:
      rounds -- number of full sweeps (the last, confirming, sweep included)
    """
    live_in = {bid: set() for bid in cfg.blocks}
    live_out = {bid: set() for bid in cfg.blocks}
    if not cfg.blocks:
        return live_in, live_out, {"rounds": 0}

    rounds = 0
    while True:
        rounds += 1
        changed = False
        for bid in sorted(cfg.blocks):
            b = cfg.blocks[bid]
            out = set()
            for dst, _kind in b.succs:
                out |= live_in[dst]
            new_in = b.uses | (out - b.defs)
            if out != live_out[bid] or new_in != live_in[bid]:
                changed = True
            live_out[bid] = out
            live_in[bid] = new_in
        if not changed:
            break
    return live_in, live_out, {"rounds": rounds}
