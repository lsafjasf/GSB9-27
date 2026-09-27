"""Differential test: worklist solver vs naive point-wise iteration.

Generates random CFGs containing loops, unreachable blocks and exceptional
edges, then asserts both solvers produce identical live-in/live-out sets
for every block.

Usage: python3 fuzz_liveness.py [num_graphs] [seed]
"""

import random
import sys

from liveness import CFG, NORMAL, EXCEPTIONAL, analyze, analyze_naive


def random_cfg(rng, n_blocks, n_vars, edge_density=2, unreachable_ratio=0.15,
               exceptional_ratio=0.2):
    cfg = CFG()
    n_reachable = max(1, int(n_blocks * (1.0 - unreachable_ratio)))
    reachable = list(range(n_reachable))
    unreachable = list(range(n_reachable, n_blocks))

    for bid in range(n_blocks):
        uses = {v for v in range(n_vars) if rng.random() < 0.35}
        defs = {v for v in range(n_vars) if rng.random() < 0.30}
        cfg.add_block(bid, uses=uses, defs=defs)

    def add_edges(bid, pool):
        k = rng.randint(0, edge_density)
        for _ in range(k):
            dst = rng.choice(pool)
            kind = EXCEPTIONAL if rng.random() < exceptional_ratio else NORMAL
            cfg.add_edge(bid, dst, kind)

    # Forward-ish random edges among reachable blocks (creates loops too,
    # since targets are chosen from the whole pool).
    for bid in reachable:
        add_edges(bid, reachable)
    # Guarantee some structured loops: nested back edges.
    for _ in range(max(1, n_reachable // 8)):
        header = rng.randrange(n_reachable)
        depth = rng.randint(1, 4)
        body = header
        for _ in range(depth):
            nxt = rng.randrange(header, n_reachable)
            cfg.add_edge(body, nxt)
            body = nxt
        cfg.add_edge(body, header, EXCEPTIONAL if rng.random() < 0.3 else NORMAL)
    # Unreachable component: its own edges (incl. loops), no path from entry.
    for bid in unreachable:
        add_edges(bid, unreachable or reachable)
    # A few exceptional edges escaping from reachable code into handlers.
    for _ in range(max(1, n_reachable // 10)):
        src = rng.choice(reachable)
        dst = rng.choice(reachable)
        cfg.add_edge(src, dst, EXCEPTIONAL)
    return cfg


def check_one(cfg, tag):
    li_fast, lo_fast, _ = analyze(cfg)
    li_naive, lo_naive, _ = analyze_naive(cfg)
    assert li_fast.keys() == li_naive.keys(), tag
    for bid in li_fast:
        assert li_fast[bid] == li_naive[bid], \
            "%s: live_in mismatch at block %r: %s vs %s" % (
                tag, bid, sorted(li_fast[bid]), sorted(li_naive[bid]))
        assert lo_fast[bid] == lo_naive[bid], \
            "%s: live_out mismatch at block %r: %s vs %s" % (
                tag, bid, sorted(lo_fast[bid]), sorted(lo_naive[bid]))


def main():
    num_graphs = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260928
    rng = random.Random(seed)
    for i in range(num_graphs):
        n_blocks = rng.choice([0, 1, 2, 3, 5, 8, 13, 21, 40, 80])
        n_vars = rng.randint(0, 12)
        cfg = random_cfg(rng, n_blocks, n_vars)
        check_one(cfg, "graph#%d (blocks=%d vars=%d)" % (i, n_blocks, n_vars))
    print("OK: %d random graphs, worklist == naive on every block" % num_graphs)


if __name__ == "__main__":
    main()
