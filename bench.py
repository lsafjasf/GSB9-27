"""Benchmark: generate a large config-driven skeleton and report timing."""

import sys
import time

from skeleton import generate


def make_config(n_classes=2000, methods_per_class=20, stmts_per_method=5,
                n_functions=2000):
    return {
        "module_doc": "Benchmark module.",
        "imports": ["os", "sys", "re", "json", "typing"],
        "constants": {"CONST_%04d" % i: i for i in range(500)},
        "classes": [
            {"name": "Class%04d" % c,
             "doc": "Class %d." % c,
             "methods": [
                 {"name": "method_%02d" % m,
                  "args": ["self", "arg%d" % m],
                  "body": ["self.slot_%d = arg%d + %d" % (s, m, s)
                           for s in range(stmts_per_method)]}
                 for m in range(methods_per_class)
             ]}
            for c in range(n_classes)
        ],
        "functions": [
            {"name": "helper_%04d" % f, "args": ["x"],
             "body": ["return x + %d" % f]}
            for f in range(n_functions)
        ],
    }


def main():
    config = make_config()
    # warmup + determinism check
    first = generate(config)
    second = generate(config)
    assert first == second, "non-deterministic output"

    runs = 5
    best = float("inf")
    for _ in range(runs):
        start = time.perf_counter()
        out = generate(config)
        best = min(best, time.perf_counter() - start)

    lines = out.count("\n")
    size = len(out.encode())
    print("config: %d classes x %d methods + %d functions + 500 constants"
          % (2000, 20, 2000))
    print("output: %d lines, %.2f MiB" % (lines, size / 2**20))
    print("best of %d runs: %.3f s (%.1f klines/s, %.1f MiB/s)"
          % (runs, best, lines / best / 1000, size / best / 2**20))
    return 0


if __name__ == "__main__":
    sys.exit(main())
