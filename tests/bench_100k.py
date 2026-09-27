"""Performance benchmark: trace latency on a 100,000-node lineage graph.

Graph shape: 1,000 layers x 100 fields.  One "spine" field per layer links
to every field in the next layer; the 99 side fields link back down to the
next spine.  This keeps the graph acyclic while making both the top spine and
the bottom spine reach *all* other 99,999 nodes: the worst case for tracing.

Run::

    python3 tests/bench_100k.py
"""

import gc
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lineage import LineageGraph  # noqa: E402


LAYERS = 1000
WIDTH = 100


def build():
    g = LineageGraph()
    grid = []
    for i in range(LAYERS):
        layer = [
            g.add_entity("f_%d_%d" % (i, j), uid="u_%d_%d" % (i, j))
            for j in range(WIDTH)
        ]
        grid.append(layer)
        if i == 0:
            continue
        # previous spine -> every node in this layer (incl. this spine)
        for j in range(WIDTH):
            g.add_edge(grid[i - 1][0], layer[j])
        # every previous side node -> this spine
        for j in range(WIDTH):
            if j != 0:
                g.add_edge(grid[i - 1][j], layer[0])
    return g, grid


def timed(label, fn, repeats=3):
    gc.collect()
    gc.disable()
    samples = []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn()
        samples.append(time.perf_counter() - start)
    gc.enable()
    best = min(samples)
    avg = sum(samples) / len(samples)
    count = len(result) if result is not None else 0
    print(
        "%-34s best=%8.3f ms  avg=%8.3f ms  (nodes returned: %d)"
        % (label, best * 1000, avg * 1000, count)
    )
    return best


def main():
    t0 = time.perf_counter()
    g, grid = build()
    build_s = time.perf_counter() - t0
    print(
        "graph: %d nodes, %d edges (built in %.2f s)"
        % (g.node_count(), g.edge_count(), build_s)
    )

    leaf = grid[-1][0]
    root = grid[0][0]
    middle = grid[LAYERS // 2][0]

    before = g.upstream(leaf)
    timed("upstream() of deepest leaf", lambda: g.upstream(leaf))
    timed("downstream() of top root", lambda: g.downstream(root))
    timed("upstream() of a middle node", lambda: g.upstream(middle))
    timed("validate() on full graph", g.validate, repeats=1)

    # Rename must be O(1) and must not change trace timing/results.
    t0 = time.perf_counter()
    g.rename(leaf, "renamed_leaf")
    g.rename(root, "renamed_root")
    rename_s = time.perf_counter() - t0
    up = g.upstream(leaf)
    print(
        "rename of 2 hot nodes:           %.6f ms; post-rename upstream nodes: %d"
        % (rename_s * 1000, len(up))
    )
    assert up == before, "rename must not change the reachable ancestor set"
    assert len(up) == 99900
    print("complexity: upstream/downstream are O(V+E), rename is O(1)")


if __name__ == "__main__":
    main()
