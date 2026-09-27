"""Structural benchmarks: convergence rounds and wall time for both solvers.

Covers: single block, empty graph, forward-only edges, deep nested loops,
and large random CFGs (>1000 blocks) with loops, unreachable blocks and
exceptional edges.

Run: python3 bench_liveness.py
"""

import random
import time

from liveness import CFG, EXCEPTIONAL, analyze, analyze_naive
from fuzz_liveness import random_cfg, check_one


def build_single_block():
    cfg = CFG()
    cfg.add_block(0, uses={"a"}, defs={"b"})
    return cfg


def build_empty():
    return CFG()


def build_forward_only(n, n_vars, rng):
    # Strict DAG: edges only from i to j with i < j.
    cfg = CFG()
    for i in range(n):
        cfg.add_block(i,
                      uses={v for v in range(n_vars) if rng.random() < 0.3},
                      defs={v for v in range(n_vars) if rng.random() < 0.3})
    for i in range(n - 1):
        cfg.add_edge(i, i + 1)
        if i + 2 < n and rng.random() < 0.3:
            cfg.add_edge(i, i + 2, EXCEPTIONAL if rng.random() < 0.2 else "normal")
    return cfg


def build_nested_loops(depth, body_size, n_vars, rng):
    # `depth` loops nested around a common innermost body; each loop level
    # has a header and a latch with a back edge to its header.
    cfg = CFG()
    bid = 0

    def new_block():
        nonlocal bid
        cfg.add_block(bid,
                      uses={v for v in range(n_vars) if rng.random() < 0.3},
                      defs={v for v in range(n_vars) if rng.random() < 0.3})
        bid += 1
        return bid - 1

    headers = []
    latches = []
    prev = None
    for _ in range(depth):
        h = new_block()
        headers.append(h)
        if prev is not None:
            cfg.add_edge(prev, h)
        prev = h
    for _ in range(body_size):
        b = new_block()
        cfg.add_edge(prev, b)
        prev = b
    for h in reversed(headers):
        latch = new_block()
        cfg.add_edge(prev, latch)
        cfg.add_edge(latch, h)          # back edge -> loop
        latches.append(latch)
        prev = latch
    exit_b = new_block()
    cfg.add_edge(prev, exit_b)
    # Loop headers also need exit edges, otherwise the loops are infinite;
    # add exceptional exits from odd headers to the exit block.
    for h in headers[1::2]:
        cfg.add_edge(h, exit_b, EXCEPTIONAL)
    return cfg


def bench(name, cfg):
    t0 = time.perf_counter()
    li_f, lo_f, st_f = analyze(cfg)
    t1 = time.perf_counter()
    li_n, lo_n, st_n = analyze_naive(cfg)
    t2 = time.perf_counter()
    assert li_f == li_n and lo_f == lo_n, "solver mismatch on %s" % name
    n = len(cfg.blocks)
    print("%-28s blocks=%5d | worklist: %6d evals (%3d sweeps) %8.2f ms"
          " | naive: %4d sweeps %9.2f ms"
          % (name, n, st_f["rounds"], st_f["sweeps"], (t1 - t0) * 1e3,
             st_n["rounds"], (t2 - t1) * 1e3))


def main():
    rng = random.Random(42)
    bench("single-block", build_single_block())
    bench("empty-graph", build_empty())
    bench("forward-only-dag-2000", build_forward_only(2000, 30, rng))
    bench("nested-loops-d200-b8", build_nested_loops(200, 8, 30, rng))
    bench("nested-loops-d500-b4", build_nested_loops(500, 4, 30, rng))
    bench("nested-loops-d1000-b2", build_nested_loops(1000, 2, 40, rng))

    for n in (1000, 2000, 4000):
        cfg = random_cfg(random.Random(1000 + n), n, n_vars=40,
                         edge_density=2, unreachable_ratio=0.15,
                         exceptional_ratio=0.2)
        check_one(cfg, "bench-%d" % n)  # differential check on the big ones too
        bench("random-%d" % n, cfg)


if __name__ == "__main__":
    main()
