"""Workload generators and a tick-based throughput simulator.

Each partition can serve at most `capacity` ops per tick (models one CPU
core per partition).  Requests arrive `arrival_rate` per tick; anything
above capacity queues.  A read of a split hot key fans out to K shard
partitions, honestly costing K sub-ops.
"""
from __future__ import annotations

import random

from partitioner import BalancedPartitioner, HashPartitioner, KVStore, distribution_metrics


# ------------------------------------------------------------------ workloads

def skewed(n, hot_key="user:celebrity", hot_share=0.8, num_keys=500, seed=1):
    """One key carries `hot_share` of all traffic; the rest is uniform."""
    rnd = random.Random(seed)
    keys = [f"user:{i}" for i in range(num_keys)]
    return [hot_key if rnd.random() < hot_share else rnd.choice(keys)
            for _ in range(n)]


def all_same(n, key="user:only-key"):
    """Degenerate case: every request hits the same key."""
    return [key] * n


def few_keys(n, num_keys=3, seed=2):
    """Fewer distinct keys than partitions, uniform over them."""
    rnd = random.Random(seed)
    keys = [f"user:{i}" for i in range(num_keys)]
    return [rnd.choice(keys) for _ in range(n)]


def drifting(n, drift_every, hot_share=0.8, num_keys=500, seed=3):
    """The hotspot moves to a new key every `drift_every` requests."""
    rnd = random.Random(seed)
    keys = [f"user:{i}" for i in range(num_keys)]
    reqs, hot_idx = [], 0
    for i in range(n):
        if i and i % drift_every == 0:
            hot_idx += 1
        hot = f"user:hot{hot_idx}"
        reqs.append(hot if rnd.random() < hot_share else rnd.choice(keys))
    return reqs


# ------------------------------------------------------------------ engine

def run_engine(router, requests, num_partitions, capacity=50, arrival_rate=200,
               read_ratio=0.1, seed=7):
    """Run `requests` through `router`; return load + throughput stats."""
    rnd = random.Random(seed)
    queues = [0] * num_partitions
    served = [0] * num_partitions
    ticks = 0
    max_queue = 0
    idx = 0
    while idx < len(requests) or any(queues):
        batch = requests[idx:idx + arrival_rate]
        idx += len(batch)
        for key in batch:
            router.observe(key)
            if read_ratio and rnd.random() < read_ratio:
                _, ops = router.read_key(key)
            else:
                ops = router.write_key(key)
            for p, _k in ops:
                queues[p] += 1
        for p in range(num_partitions):
            s = min(capacity, queues[p])
            queues[p] -= s
            served[p] += s
        if queues:
            max_queue = max(max_queue, max(queues))
        ticks += 1
    return {
        "served": served,
        "key_counts": [len(router.store.parts[p]) for p in range(num_partitions)],
        "ticks": ticks,
        "max_queue": max_queue,
        "throughput": sum(served) / ticks,          # ops per tick
        "total_ops": sum(served),
    }


def build_router(mode, num_partitions, **kw):
    if mode == "baseline":
        return HashPartitioner(num_partitions, KVStore(num_partitions))
    return BalancedPartitioner(num_partitions, KVStore(num_partitions), **kw)


def compare(name, requests, num_partitions=16, capacity=50, arrival_rate=200,
            read_ratio=0.1, **kw):
    """Run the same workload through baseline and fixed; return both reports."""
    out = {}
    for mode in ("baseline", "fixed"):
        router = build_router(mode, num_partitions, **kw)
        stats = run_engine(router, requests, num_partitions, capacity,
                           arrival_rate, read_ratio)
        out[mode] = {
            "router": router,
            "stats": stats,
            "req_metrics": distribution_metrics(stats["served"]),
            "key_metrics": distribution_metrics(stats["key_counts"]),
        }
    return out
