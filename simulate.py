"""Reproduce the hotspot, then show the fix: skew metrics + throughput.

Usage: python3 simulate.py
"""
from __future__ import annotations

from simulator import compare, skewed, all_same, few_keys, drifting

N = 16            # partitions
REQS = 20_000     # requests per scenario


def fmt(x):
    if x == float("inf"):
        return "  inf"
    return f"{x:5.2f}" if isinstance(x, float) else f"{x:5d}"


def show(name, rep):
    print(f"\n=== {name} ===")
    print(f"{'':14s}{'scheme':10s}{'min':>6s}{'median':>8s}{'max':>7s}"
          f"{'max/med':>9s}{'max/mean':>9s}{'Jain':>7s}{'top%':>7s}{'active':>8s}")
    for mode in ("baseline", "fixed"):
        for label, m in (("reqs/part", rep[mode]["req_metrics"]),
                         ("keys/part", rep[mode]["key_metrics"])):
            tag = f"{mode}/{label}"
            print(f"{tag:14s}{'':10s}{m['min']:6.0f}{m['median']:8.1f}{m['max']:7.0f}"
                  f"{fmt(m['max_over_median']):>9s}{m['max_over_mean']:9.2f}"
                  f"{m['jain']:7.3f}{m['top_share'] * 100:6.1f}%{m['active']:8d}")
    b, f = rep["baseline"]["stats"], rep["fixed"]["stats"]
    print(f"throughput(ops/tick): baseline={b['throughput']:.1f}  fixed={f['throughput']:.1f}")
    print(f"ticks to drain:       baseline={b['ticks']}  fixed={f['ticks']}")
    print(f"max queue depth:      baseline={b['max_queue']}  fixed={f['max_queue']}")


def main():
    show("skewed: one key = 80% of traffic",
         compare("skewed", skewed(REQS), N))
    show("all keys identical",
         compare("all-same", all_same(REQS), N))
    show("3 keys < 16 partitions",
         compare("few-keys", few_keys(REQS, num_keys=3), N))
    show("hotspot drifts every 5000 reqs",
         compare("drifting", drifting(REQS, drift_every=5000), N))


if __name__ == "__main__":
    main()
