"""Cross-check the DP selector against the exhaustive brute-force selector
on many small random expression DAGs (including shared subexpressions and
div/mod pairs that exercise multi-output instructions).

Usage: python3 compare.py [trials] [max_nodes]
"""

import random
import sys

from iselect import select
from brute_force import brute_force
from isa import default_isa

OPS = ["add", "add", "mul", "sub", "neg", "div", "mod"]


def random_dag(rng, n_nodes, extra_roots=0):
    """Build a random DAG bottom-up; children are reused => real sharing."""
    nodes = []
    for _ in range(n_nodes):
        r = rng.random()
        if not nodes or r < 0.35:
            if rng.random() < 0.5:
                nodes.append(Node("const", val=rng.randint(1, 16)))
            else:
                nodes.append(Node("reg", val=rng.randrange(4)))
            continue
        op = rng.choice(OPS)
        if op == "neg":
            nodes.append(Node("neg", (rng.choice(nodes),)))
            continue
        kids = (rng.choice(nodes), rng.choice(nodes))
        nodes.append(Node(op, kids))
        if op == "div" and rng.random() < 0.5:
            nodes.append(Node("mod", kids))  # creates divmod opportunities
    roots = [nodes[-1]]
    for _ in range(extra_roots):
        roots.append(rng.choice(nodes))
    return roots


from iselect import Node  # noqa: E402  (import after function use is fine)


def main(trials=500, max_nodes=9, seed0=0):
    instrs = default_isa()
    mismatches = 0
    for t in range(trials):
        rng = random.Random(seed0 + t)
        roots = random_dag(rng, rng.randint(2, max_nodes), rng.randint(0, 1))
        sel = select(roots, instrs)
        want = brute_force(roots, instrs)
        step_sum = sum(s.cost for s in sel.steps)
        if sel.total_cost != want or step_sum != sel.total_cost:
            mismatches += 1
            print("MISMATCH seed=%d dp=%s brute=%s step_sum=%s"
                  % (seed0 + t, sel.total_cost, want, step_sum))
            for r in roots:
                print("  root:", r)
    print("compared %d random DAGs (up to %d nodes); mismatches: %d"
          % (trials, max_nodes, mismatches))
    return 1 if mismatches else 0


if __name__ == "__main__":
    args = [int(x) for x in sys.argv[1:]]
    sys.exit(main(*args))
