"""Benchmark: trace a 100k-node lineage graph.

Run: python3 -m lineage.perf
"""

import random
import time

from .lineage import LineageGraph

NODES = 100_000
LAYERS = 10
FAN_IN = 3


def build_graph():
    rng = random.Random(42)
    g = LineageGraph()
    per_layer = NODES // LAYERS
    layers = []
    for layer in range(LAYERS):
        ids = [g.add_field(f"f{layer}_{i}") for i in range(per_layer)]
        layers.append(ids)
    for layer in range(1, LAYERS):
        prev = layers[layer - 1]
        for fid in layers[layer]:
            for up in rng.sample(prev, FAN_IN):
                # bulk load: skip per-edge cycle check, validate() once below
                g.add_edge(up, fid, check_cycle=False)
    return g, layers


def main():
    t0 = time.perf_counter()
    g, layers = build_graph()
    t1 = time.perf_counter()
    print(f"nodes={len(g)}  build={t1 - t0:.3f}s")

    g.validate()
    t2 = time.perf_counter()
    print(f"validate(acyclic+no-dangling)={t2 - t1:.3f}s")

    leaf = layers[-1][-1]
    t3 = time.perf_counter()
    ups = g.upstream(leaf)
    t4 = time.perf_counter()
    print(f"upstream(leaf): {len(ups)} ancestors in {t4 - t3:.4f}s")

    root = layers[0][0]
    t5 = time.perf_counter()
    downs = g.downstream(root)
    t6 = time.perf_counter()
    print(f"downstream(root): {len(downs)} impacted in {t6 - t5:.4f}s")

    # worst case: full-graph sweep from every node in the first layer
    t7 = time.perf_counter()
    total = sum(len(g.downstream(fid)) for fid in layers[0])
    t8 = time.perf_counter()
    print(f"full sweep from {len(layers[0])} roots: {total} hits in {t8 - t7:.3f}s")


if __name__ == "__main__":
    main()
