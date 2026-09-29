"""Differential test: fast dominator algorithm vs brute-force
reachability, on randomly generated CFGs.

Covers self loops, multiple exits and unreachable blocks by
construction.  Usage: python3 tests/stress.py [num_graphs] [seed]
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dom.cfg import CFG, Block
from dom import (compute_idom, dominator_sets, dominance_frontiers,
                 brute_dominator_sets, brute_idom,
                 brute_dominance_frontiers)


def random_cfg(rng, n):
    """Random CFG with n blocks named B0..B{n-1}; B0 is the entry.

    Edges are arbitrary (forward, backward, self loops); out-degree is
    0..2, so graphs may have several exit blocks and unreachable parts.
    """
    cfg = CFG()
    names = ["B%d" % i for i in range(n)]
    for name in names:
        cfg.add_block(Block(name))
    cfg.entry = "B0"
    for name in names:
        out_deg = rng.choice((0, 1, 1, 2, 2, 2))
        succs = []
        for _ in range(out_deg):
            tgt = rng.choice(names)
            if tgt not in succs:
                succs.append(tgt)
        cfg.blocks[name].succs = succs
        for tgt in succs:
            cfg.blocks[tgt].preds.append(name)
    return cfg


def check(cfg, gid):
    idom = compute_idom(cfg)
    doms = dominator_sets(idom)
    ref_doms = brute_dominator_sets(cfg)
    ref_idom = brute_idom(cfg)

    assert doms == ref_doms, "graph %d: dom sets differ\nfast=%s\nbrute=%s" \
        % (gid, doms, ref_doms)
    assert idom == ref_idom, "graph %d: idom differs\nfast=%s\nbrute=%s" \
        % (gid, idom, ref_idom)

    # every block in the analysis must be reachable, and vice versa
    reachable = cfg.reachable_names()
    assert set(idom) == reachable, "graph %d: reachable mismatch" % gid

    # dominance frontiers must match the definitional brute-force
    # computation exactly (set equality, both directions)
    df = dominance_frontiers(cfg, idom)
    ref_df = brute_dominance_frontiers(cfg, ref_doms)
    assert df == ref_df, "graph %d: DF differs\nfast=%s\nbrute=%s" \
        % (gid, df, ref_df)


def main():
    num = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260928
    rng = random.Random(seed)
    stats = {"self_loop": 0, "multi_exit": 0, "unreachable": 0}
    for gid in range(num):
        n = rng.randint(1, 24)
        cfg = random_cfg(rng, n)
        if any(n in cfg.blocks[n].succs for n in cfg.order):
            stats["self_loop"] += 1
        exits = [n for n in cfg.reachable_names()
                 if not cfg.blocks[n].succs]
        if len(exits) > 1:
            stats["multi_exit"] += 1
        if cfg.unreachable_names():
            stats["unreachable"] += 1
        check(cfg, gid)
    print("PASS: %d random graphs (seed=%d)" % (num, seed))
    print("  with self loops        : %d" % stats["self_loop"])
    print("  with multiple exits    : %d" % stats["multi_exit"])
    print("  with unreachable blocks: %d" % stats["unreachable"])


if __name__ == "__main__":
    main()
