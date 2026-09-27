"""Build a large CFG (default 20000 blocks: chains of diamonds with
nested loop back-edges) and time each analysis phase."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dom import (parse_tac, compute_idom, dominator_sets, dominator_tree,
                 dominance_frontiers)


def gen_large_tac(n_diamonds):
    """Each diamond: head -> (left, right) -> join; every 8th join jumps
    back to an earlier head, creating nested loops.  4 blocks per diamond."""
    lines = []
    heads = []
    for i in range(n_diamonds):
        h, l, r, j = "h%d" % i, "l%d" % i, "r%d" % i, "j%d" % i
        heads.append(h)
        lines.append("%s:" % h)
        lines.append("  x = x + 1")
        lines.append("  if x > 0 goto %s else goto %s" % (l, r))
        lines.append("%s:" % l)
        lines.append("  y = x + 1")
        lines.append("  goto %s" % j)
        lines.append("%s:" % r)
        lines.append("  y = x - 1")
        lines.append("  goto %s" % j)
        lines.append("%s:" % j)
        lines.append("  z = y * 2")
        nxt = "h%d" % (i + 1) if i + 1 < n_diamonds else "exit"
        if i and i % 8 == 0:
            lines.append("  if z > 0 goto %s else goto %s"
                         % (heads[i - 8], nxt))
        else:
            lines.append("  goto %s" % nxt)
    lines.append("exit:")
    lines.append("  return")
    return "\n".join(lines)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n_diamonds = int(args[0]) if args else 2500
    want_sets = "--dom-sets" in sys.argv   # O(n^2) output, off by default
    src = gen_large_tac(n_diamonds)

    t0 = time.perf_counter()
    cfg = parse_tac(src)
    t1 = time.perf_counter()
    idom = compute_idom(cfg)
    t2 = time.perf_counter()
    tree = dominator_tree(idom)
    df = dominance_frontiers(cfg, idom)
    t3 = time.perf_counter()
    if want_sets:
        dominator_sets(idom)
    t4 = time.perf_counter()

    n_blocks = len(cfg.order)
    n_edges = sum(len(cfg.blocks[n].succs) for n in cfg.order)
    print("blocks=%d  edges=%d  unreachable=%d"
          % (n_blocks, n_edges, len(cfg.unreachable_names())))
    print("parse + build CFG : %8.1f ms" % ((t1 - t0) * 1e3))
    print("idom (CHK)        : %8.1f ms" % ((t2 - t1) * 1e3))
    print("dom tree + DF     : %8.1f ms" % ((t3 - t2) * 1e3))
    if want_sets:
        print("dom sets (O(n^2)) : %8.1f ms" % ((t4 - t3) * 1e3))
    print("total             : %8.1f ms" % ((t4 - t0) * 1e3))


if __name__ == "__main__":
    main()
