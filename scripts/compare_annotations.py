#!/usr/bin/env python3
"""Compare metric ranking against human annotation ranking.

Usage:  python3 scripts/compare_annotations.py
Exit code 0 iff Spearman rho >= 0.90 (i.e. the metric reproduces the
human maintainability ranking on the annotated sample).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codemetrics.complexity import analyze_file
from codemetrics.stats import kendall_tau_b, spearman

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "..", "samples", "annotated.py")
ANNOTATIONS = os.path.join(HERE, "..", "samples", "annotations.json")
THRESHOLD = 0.90


def main():
    with open(ANNOTATIONS, encoding="utf-8") as fh:
        annotations = json.load(fh)["functions"]
    metrics = {f.qualname: f for f in analyze_file(SAMPLE).functions}

    names = sorted(annotations)
    missing = [n for n in names if n not in metrics]
    if missing:
        print("MISSING FUNCTIONS:", missing)
        return 1

    human = [annotations[n]["human"] for n in names]
    metric = [metrics[n].complexity for n in names]

    print(f"{'function':28s} {'human':>5} {'metric':>6} {'loc':>4}")
    for n in sorted(names, key=lambda n: -annotations[n]["human"]):
        print(f"{n:28s} {annotations[n]['human']:>5} "
              f"{metrics[n].complexity:>6} {metrics[n].loc:>4}")

    rho = spearman(human, metric)
    tau = kendall_tau_b(human, metric)
    print(f"\nSpearman rho = {rho:.4f}")
    print(f"Kendall tau-b = {tau:.4f}")
    ok = rho >= THRESHOLD
    print(f"threshold rho >= {THRESHOLD}: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
