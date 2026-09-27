#!/usr/bin/env python3
"""Reproduce the hot-partition skew, then show the fix.

Run: python3 main.py
"""

import random

from partitioning import (
    BucketMigration,
    Cluster,
    HotKeyDetector,
    ModuloRouter,
    ShardedRouter,
    assert_placement_consistent,
    distribution_metrics,
    run_workload,
    throughput_metrics,
)


def make_keys(n, prefix="key"):
    return [f"{prefix}-{i}" for i in range(n)]


def hot_key_stream(keys, n_requests, hot_key, hot_share, rng):
    others = [k for k in keys if k != hot_key]
    stream = []
    for _ in range(n_requests):
        if rng.random() < hot_share or not others:
            stream.append(hot_key)
        else:
            stream.append(rng.choice(others))
    return stream


def fmt_ratio(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


def print_metrics(label, m, t):
    print(f"  {label:<28} keys={m['total']:>7}  max={m['max']:>7}  "
          f"median={m['median']:>7.0f}  max/median={fmt_ratio(m['max/median']):>7}  "
          f"max/mean={m['max/mean']:.2f}  cv={m['cv']:.2f}  "
          f"eff_parts={m['effective_partitions']:.2f}/{m['partitions']}  "
          f"served={t['served_ratio'] * 100:5.1f}%")


def print_counts(counts, title):
    total = sum(counts) or 1
    print(f"  {title}")
    width = max(counts) or 1
    for pid, c in enumerate(counts):
        bar = "#" * round(40 * c / width)
        print(f"    p{pid}: {c:>7} ({c / total * 100:5.1f}%) {bar}")


def report(tag, cluster):
    req = distribution_metrics(cluster.req_counts)
    keys = distribution_metrics(cluster.key_counts())
    tput = throughput_metrics(cluster.req_counts)
    print_metrics(f"{tag} requests", req, tput)
    print(f"  {tag:<28} keys hosted per partition: {cluster.key_counts()}  "
          f"max/median={fmt_ratio(keys['max/median'])}")
    return req, tput


def scenario_repro():
    print("=" * 100)
    print("SCENARIO A: reproduce the hot spot (8 partitions, 2000 keys, "
          "100k requests, one key takes 40%)")
    print("=" * 100)
    rng = random.Random(42)
    keys = make_keys(2000)
    stream = hot_key_stream(keys, 100_000, hot_key="key-7", hot_share=0.40, rng=rng)

    before = Cluster(ModuloRouter(8))
    run_workload(before, stream, rng=random.Random(1))
    print("\n[BEFORE] hash(key) % 8")
    print_counts(before.req_counts, "requests per partition")
    report("before", before)

    after = Cluster(ShardedRouter(8, num_buckets=256))
    detector = HotKeyDetector()
    run_workload(after, stream, detector=detector, window=10_000,
                 rng=random.Random(1))
    print("\n[AFTER] virtual buckets + hot-key splitting "
          f"(hot keys now: { {k: v for k, v in after.router.hot.items()} })")
    print_counts(after.req_counts, "requests per partition")
    report("after", after)
    assert_placement_consistent(after)


def scenario_identical_keys():
    print("\n" + "=" * 100)
    print("SCENARIO B: all keys identical (single key hammered, 8 partitions, 50k requests)")
    print("=" * 100)
    stream = ["only-key"] * 50_000

    before = Cluster(ModuloRouter(8))
    run_workload(before, stream, rng=random.Random(2))
    report("before", before)

    after = Cluster(ShardedRouter(8, num_buckets=256))
    run_workload(after, stream, detector=HotKeyDetector(), window=5_000,
                 rng=random.Random(2))
    report("after", after)
    print(f"  after: 'only-key' split into {after.router.hot.get('only-key')} shards; "
          f"read-your-writes check: {after.read('only-key') is not None}")
    assert_placement_consistent(after)


def scenario_few_keys():
    print("\n" + "=" * 100)
    print("SCENARIO C: fewer keys than partitions (3 keys, 8 partitions, 60k requests)")
    print("=" * 100)
    rng = random.Random(7)
    keys = make_keys(3)
    stream = hot_key_stream(keys, 60_000, hot_key="key-0", hot_share=0.70, rng=rng)

    before = Cluster(ModuloRouter(8))
    run_workload(before, stream, rng=random.Random(3))
    report("before", before)

    after = Cluster(ShardedRouter(8, num_buckets=256))
    run_workload(after, stream, detector=HotKeyDetector(), window=6_000,
                 rng=random.Random(3))
    report("after", after)
    assert_placement_consistent(after)


def scenario_drifting_hot_key():
    print("\n" + "=" * 100)
    print("SCENARIO D: hot spot drifts over time (hot key rotates every phase)")
    print("=" * 100)
    n_phases, per_phase = 4, 25_000
    keys = make_keys(500)

    before = Cluster(ModuloRouter(8))
    after = Cluster(ShardedRouter(8, num_buckets=256))
    detector = HotKeyDetector()

    print(f"  {'phase':<6} {'hot key':<10} {'before max/mean':>16} "
          f"{'before served':>14} {'after max/mean':>15} {'after served':>13}  hot keys after phase")
    for phase in range(n_phases):
        hot_key = keys[phase]
        stream = hot_key_stream(keys, per_phase, hot_key, 0.50,
                                rng=random.Random(100 + phase))
        b = Cluster(ModuloRouter(8))
        run_workload(b, stream, rng=random.Random(phase))
        bm = distribution_metrics(b.req_counts)
        bt = throughput_metrics(b.req_counts)

        a_counts_before = list(after.req_counts)
        run_workload(after, stream, detector=detector, window=per_phase // 5,
                     rng=random.Random(phase))
        phase_counts = [x - y for x, y in zip(after.req_counts, a_counts_before)]
        am = distribution_metrics(phase_counts)
        at = throughput_metrics(phase_counts)
        print(f"  {phase:<6} {hot_key:<10} {bm['max/mean']:>16.2f} "
              f"{bt['served_ratio'] * 100:>13.1f}% {am['max/mean']:>15.2f} "
              f"{at['served_ratio'] * 100:>12.1f}%  hot={dict(after.router.hot)}")
        assert_placement_consistent(after)


def scenario_migration():
    print("\n" + "=" * 100)
    print("SCENARIO E: reentrant bucket migration with crash injection")
    print("=" * 100)
    router = ShardedRouter(8, num_buckets=64)
    cluster = Cluster(router)
    rng = random.Random(9)
    for i in range(500):
        cluster.write(f"k{i}", f"v{i}")
    bucket = 3
    src = router.bucket_table[bucket]
    dst = (src + 3) % 8
    print(f"  moving bucket {bucket}: partition {src} -> {dst}")
    for crash_before in range(3):
        router2 = ShardedRouter(8, num_buckets=64)
        c2 = Cluster(router2)
        for i in range(500):
            c2.write(f"k{i}", f"v{i}")
        mig = BucketMigration(c2, bucket, dst)
        step_no = 0
        while mig.state != BucketMigration.DONE:
            if step_no == crash_before:
                mig.recover()
                print(f"    crash injected before step {step_no}: "
                      f"recover() -> {mig.state}")
            mig.step()
            assert_placement_consistent(c2)
            step_no += 1
        assert mig.state == BucketMigration.DONE
        vals = {c2.read(f"k{i}") for i in range(500)}
        assert vals == {f"v{i}" for i in range(500)}, "data lost during migration"
    print("  invariant held after every step and after every crash/recover: "
          "each storage key owned by exactly one partition, no data lost")


if __name__ == "__main__":
    scenario_repro()
    scenario_identical_keys()
    scenario_few_keys()
    scenario_drifting_hot_key()
    scenario_migration()
