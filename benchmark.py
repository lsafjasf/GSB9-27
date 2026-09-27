"""Benchmarks for the hash ring: migration ratio, balance curve, lookup perf.

Run: python3 benchmark.py
Writes: migration_report.txt, distribution_curve.csv, perf_report.txt
"""

import csv
import statistics
import time

from consistent_hash import HashRing

KEY_COUNT = 200_000
LOOKUP_COUNT = 1_000_000
NODE_COUNT = 10


def build_ring(node_weights, vnodes):
    ring = HashRing(vnodes_per_weight=vnodes)
    for node_id, weight in node_weights:
        ring.add_node(node_id, weight)
    return ring


def map_keys(ring, keys):
    return {key: ring.get_node(key) for key in keys}


def migration_experiment(keys):
    lines = []
    lines.append("=== Migration ratio: measured vs theoretical lower bound ===")
    lines.append("nodes=%d equal-weight, keys=%d, vnodes_per_weight=160" % (NODE_COUNT, len(keys)))
    header = "%-22s %12s %12s %14s" % ("operation", "measured", "theory_min", "unrelated_mv")
    lines.append(header)

    base = [("n%d" % i, 1.0) for i in range(NODE_COUNT)]

    # --- add one equal-weight node: theory = 1/(N+1) ---
    ring = build_ring(base, 160)
    before = map_keys(ring, keys)
    ring.add_node("n-new", 1.0)
    after = map_keys(ring, keys)
    moved = unrelated = 0
    for key in keys:
        if before[key] != after[key]:
            moved += 1
            if after[key] != "n-new":
                unrelated += 1
    lines.append("%-22s %11.4f%% %11.4f%% %14d" % (
        "add 1 node (w=1)", 100 * moved / len(keys),
        100 * 1 / (NODE_COUNT + 1), unrelated))

    # --- remove one node: theory = 1/N ---
    ring = build_ring(base, 160)
    before = map_keys(ring, keys)
    ring.remove_node("n3")
    after = map_keys(ring, keys)
    moved = unrelated = 0
    for key in keys:
        if before[key] != after[key]:
            moved += 1
            if before[key] != "n3":
                unrelated += 1
    lines.append("%-22s %11.4f%% %11.4f%% %14d" % (
        "remove 1 node (w=1)", 100 * moved / len(keys),
        100 * 1 / NODE_COUNT, unrelated))

    # --- add a heavy node w=3 to 10 units: theory = 3/13 ---
    ring = build_ring(base, 160)
    before = map_keys(ring, keys)
    ring.add_node("n-heavy", 3.0)
    after = map_keys(ring, keys)
    moved = unrelated = 0
    for key in keys:
        if before[key] != after[key]:
            moved += 1
            if after[key] != "n-heavy":
                unrelated += 1
    lines.append("%-22s %11.4f%% %11.4f%% %14d" % (
        "add 1 node (w=3)", 100 * moved / len(keys),
        100 * 3 / (NODE_COUNT + 3), unrelated))

    lines.append("")
    lines.append("unrelated_mv = keys that moved between two UNAFFECTED nodes "
                 "(must be 0: consistent hashing never migrates them).")
    lines.append("theory_min = share of key space the added/removed node must own:")
    lines.append("  add w to total W -> w/(W+w);  remove w from W -> w/W.")
    return "\n".join(lines)


def distribution_curve(seeds=5):
    """Measured balance quality vs vnodes_per_weight, exact arc-length shares.

    Each vnode count is measured with ``seeds`` independent node-name sets
    (different hash placements) and averaged, to smooth sampling noise.
    """
    rows = []
    for vnodes in (5, 10, 25, 50, 100, 160, 250, 500, 1000):
        max_devs, cvs, mins, maxs = [], [], [], []
        ring_points = 0
        for seed in range(seeds):
            members = [("s%d-n%d" % (seed, i), 1.0) for i in range(NODE_COUNT)]
            ring = build_ring(members, vnodes)
            ring_points = ring.ring_size
            shares = list(ring.ownership().values())
            ideal = 1.0 / NODE_COUNT
            max_devs.append(max(abs(s - ideal) / ideal for s in shares))
            cvs.append(statistics.pstdev(shares) / ideal)
            mins.append(min(shares))
            maxs.append(max(shares))
        rows.append({
            "vnodes_per_weight": vnodes,
            "ring_points": ring_points,
            "ideal_share": round(1.0 / NODE_COUNT, 6),
            "min_share": round(statistics.mean(mins), 6),
            "max_share": round(statistics.mean(maxs), 6),
            "max_rel_deviation_pct": round(100 * statistics.mean(max_devs), 2),
            "cv_pct": round(100 * statistics.mean(cvs), 2),
        })
    return rows


def perf_experiment():
    lines = []
    lines.append("=== Lookup performance ===")
    for vnodes in (160, 1000):
        ring = build_ring([("n%d" % i, 1.0) for i in range(NODE_COUNT)], vnodes)
        keys = ["perf-key-%d" % i for i in range(LOOKUP_COUNT)]
        ring.get_node("warmup")
        start = time.perf_counter()
        for key in keys:
            ring.get_node(key)
        elapsed = time.perf_counter() - start
        lines.append("nodes=%d vnodes/weight=%d ring_points=%d: "
                     "%d lookups in %.3fs -> %.0f ns/op (%.2f M lookups/s)" % (
                         NODE_COUNT, vnodes, ring.ring_size, LOOKUP_COUNT,
                         elapsed, 1e9 * elapsed / LOOKUP_COUNT,
                         LOOKUP_COUNT / elapsed / 1e6))
    return "\n".join(lines)


def main():
    keys = ["key-%d" % i for i in range(KEY_COUNT)]

    migration_report = migration_experiment(keys)
    print(migration_report)
    with open("migration_report.txt", "w") as fh:
        fh.write(migration_report + "\n")

    rows = distribution_curve()
    with open("distribution_curve.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print("\n=== Distribution deviation vs vnode count (exact arc shares, %d nodes) ==="
          % NODE_COUNT)
    print("%-8s %-8s %-9s %-9s %-9s %s" % (
        "vnodes", "points", "min", "max", "max_dev%", "cv%"))
    for row in rows:
        print("%-8d %-8d %-9.4f %-9.4f %-9.2f %.2f" % (
            row["vnodes_per_weight"], row["ring_points"], row["min_share"],
            row["max_share"], row["max_rel_deviation_pct"], row["cv_pct"]))

    perf_report = perf_experiment()
    print("\n" + perf_report)
    with open("perf_report.txt", "w") as fh:
        fh.write(perf_report + "\n")


if __name__ == "__main__":
    main()
