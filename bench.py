"""Scale & timing benchmark + conflict path samples.  Run: python3 bench.py"""

import time

from tree_move import BatchMoveError, Forest, MoveError


def build_chain(forest, prefix, n):
    forest.add_root(prefix + "0")
    for i in range(1, n):
        forest.add_child(prefix + str(i - 1), prefix + str(i))


def timed(fn, repeat=1):
    """Return (best_ms, result) over `repeat` runs."""
    best = float("inf")
    result = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        best = min(best, (time.perf_counter() - t0) * 1000.0)
    return best, result


def row(name, ms, extra=""):
    print("  %-58s %10.3f ms   %s" % (name, ms, extra))


def demo_conflict_paths():
    print("== conflict path samples ==")
    f = Forest()
    for nid in ["r", "a", "b", "c"]:
        if nid == "r":
            f.add_root(nid)
        else:
            f.add_child({"a": "r", "b": "a", "c": "b"}[nid], nid)
    g = Forest()
    g.add_root("x")
    g.add_child("x", "y")

    cases = [
        ("move b under b (self)", f, "b", "b"),
        ("move a under b (direct child)", f, "a", "b"),
        ("move r under c (deep descendant)", f, "r", "c"),
    ]
    for label, forest, nid, tid in cases:
        try:
            forest.move(nid, tid)
        except MoveError as e:
            print("  %-34s rejected, conflict path: %s"
                  % (label, " -> ".join(e.path)))

    # composed cycle across two trees, only visible at batch level
    h = Forest()
    h.add_root("t1")
    h.add_child("t1", "u")
    h.add_root("t2")
    h.add_child("t2", "v")
    try:
        h.apply_batch([("t1", "v"), ("t2", "u")])
    except BatchMoveError as e:
        print("  %-34s rejected, composed cycle: %s"
              % ("batch t1->v, t2->u", " -> ".join(e.cycle_path)))
    print()


def main():
    demo_conflict_paths()

    N = 100_000
    print("== deep chain: %d nodes (recursion limit untouched) ==" % N)
    f = Forest()
    ms, _ = timed(lambda: build_chain(f, "c", N))
    row("build chain", ms)

    mid = "c%d" % (N // 2)
    ms, _ = timed(lambda: f.move(mid, "c0"))
    row("move middle (50k-node subtree) under root", ms)
    ms, _ = timed(lambda: f.move(mid, "c%d" % (N // 2 - 1)))
    row("move it back", ms)

    deepest = "c%d" % (N - 1)
    ms, plen = timed(lambda: len(f.path(deepest)), repeat=50)
    row("path() query at depth %d" % (plen - 1), ms, "avg of 50")
    ms, _ = timed(f.check_invariants)
    row("check_invariants() on %d nodes" % N, ms)
    print()

    print("== merge two %d-node chains -> %d-deep tree ==" % (N, 2 * N))
    g = Forest()
    build_chain(g, "a", N)
    build_chain(g, "b", N)
    ms, _ = timed(lambda: g.move("b0", "a%d" % (N - 1)))
    row("move root of chain B under deepest of A", ms)
    deepest = "b%d" % (N - 1)
    ms, plen = timed(lambda: len(g.path(deepest)), repeat=20)
    row("path() query at depth %d" % (plen - 1), ms, "avg of 20")
    ms, _ = timed(g.check_invariants)
    row("check_invariants() on %d nodes" % (2 * N), ms)
    print()

    W = 100_000
    print("== wide tree: root + %d children ==" % W)
    w = Forest()
    w.add_root("root")
    ms, _ = timed(lambda: [w.add_child("root", "w%d" % i) for i in range(W)])
    row("build wide tree", ms)
    ms, _ = timed(lambda: w.move("w0", "w%d" % (W - 1)))
    row("move one child under another (O(#siblings) detach)", ms)
    ms, _ = timed(w.check_invariants)
    row("check_invariants() on %d nodes" % (W + 1), ms)
    print()

    print("== batch settlement on %d-node chain ==" % N)
    b = Forest()
    build_chain(b, "c", N)
    moves = [("c%d" % i, "c0") for i in range(100, N, 100)]  # ~1000 moves
    ms, _ = timed(lambda: b.apply_batch(moves))
    row("apply_batch of %d moves" % len(moves), ms)
    ms, _ = timed(b.check_invariants)
    row("check_invariants() after batch", ms)
    # order independence at scale: reversed batch on a fresh copy
    b2 = Forest()
    build_chain(b2, "c", N)
    ms, _ = timed(lambda: b2.apply_batch(list(reversed(moves))))
    same = all(
        (n1.parent is None and n2.parent is None)
        or (n1.parent and n2.parent and n1.parent.id == n2.parent.id)
        for n1, n2 in zip(
            (b.nodes[i] for i in b.nodes), (b2.nodes[i] for i in b2.nodes))
    ) and b.nodes.keys() == b2.nodes.keys()
    row("apply_batch reversed order", ms, "same result: %s" % same)


if __name__ == "__main__":
    main()
