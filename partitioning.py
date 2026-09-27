"""Skewed partitioning: reproduction, metrics, and a hot-key-splitting fix.

Python 3, standard library only.

Routers
-------
ModuloRouter   : the "before" scheme, hash(key) % N. A single hot key
                 saturates one partition while the rest idle.
ShardedRouter  : the "after" scheme. Cold keys map through virtual buckets
                 (bucket = hash(key) % V, bucket -> partition table), so the
                 same cold key always lands on the same partition. A key
                 detected as hot is split into S sub-keys "key#shard{i}"
                 scattered by hash; writes pick a shard round-robin and reads
                 fan out to all S shards and merge by version. This knowingly
                 breaks the "same key -> same partition" guarantee for hot
                 keys only; the compensation is the versioned scatter-gather
                 read, which preserves a single logical-key view and
                 read-your-writes at the API level.

Migrations
----------
BucketMigration / SplitMigration / DemoteMigration are reentrant state
machines (PREPARE -> FLIP -> CLEANUP -> DONE). Each step is atomic; a crash
may happen between steps and recover() plus idempotent steps drive the
migration to DONE. The invariant asserted everywhere: a storage key is
served by exactly one partition at any time (shadow copies staged during
PREPARE live outside the serving dicts and are never read).
"""

from __future__ import annotations

import hashlib
import math
import statistics
from collections import Counter, defaultdict


def stable_hash(key: str) -> int:
    return int.from_bytes(hashlib.md5(key.encode("utf-8")).digest()[:8], "big")


def shard_storage_key(key: str, index: int) -> str:
    return f"{key}#shard{index}"


# ---------------------------------------------------------------- metrics

def distribution_metrics(counts) -> dict:
    counts = list(counts)
    n = len(counts)
    if n == 0:
        raise ValueError("counts must be non-empty")
    total = sum(counts)
    ordered = sorted(counts)
    median = statistics.median(counts)
    mean = total / n
    p99 = ordered[min(n - 1, math.ceil(0.99 * n) - 1)]
    mx = ordered[-1]
    variance = sum((c - mean) ** 2 for c in counts) / n
    cv = math.sqrt(variance) / mean if mean else 0.0
    hhi = sum((c / total) ** 2 for c in counts) if total else 0.0
    return {
        "partitions": n,
        "total": total,
        "min": ordered[0],
        "median": median,
        "mean": mean,
        "p99": p99,
        "max": mx,
        "max/median": (mx / median) if median else (float("inf") if mx else 1.0),
        "max/mean": (mx / mean) if mean else 1.0,
        "cv": cv,
        "effective_partitions": (1.0 / hhi) if hhi else 0.0,
    }


def throughput_metrics(counts, headroom: float = 1.2) -> dict:
    """Each partition serves at most `headroom * mean` requests; the rest
    queue/drop. Models one saturated CPU while the other partitions idle."""
    counts = list(counts)
    total = sum(counts)
    n = len(counts)
    capacity = math.ceil(headroom * total / n) if n else 0
    served = sum(min(c, capacity) for c in counts)
    return {
        "capacity_per_partition": capacity,
        "served": served,
        "total": total,
        "served_ratio": (served / total) if total else 1.0,
    }


# ---------------------------------------------------------------- routers

class ModuloRouter:
    """Before: hash(key) % N."""

    def __init__(self, num_partitions: int):
        self.num_partitions = num_partitions

    def write_location(self, key: str):
        return [(stable_hash(key) % self.num_partitions, key)]

    def read_locations(self, key: str):
        return [(stable_hash(key) % self.num_partitions, key)]


class ShardedRouter:
    """After: virtual buckets for cold keys, salted sub-keys for hot keys."""

    def __init__(self, num_partitions: int, num_buckets: int = 256):
        if num_buckets < num_partitions:
            raise ValueError("num_buckets must be >= num_partitions")
        self.num_partitions = num_partitions
        self.num_buckets = num_buckets
        self.bucket_table = [b % num_partitions for b in range(num_buckets)]
        self.hot: dict[str, int] = {}
        self._rr: dict[str, int] = defaultdict(int)

    def bucket_owner(self, bucket: int) -> int:
        return self.bucket_table[bucket]

    def storage_owner(self, storage_key: str) -> int:
        return self.bucket_table[stable_hash(storage_key) % self.num_buckets]

    def write_location(self, key: str):
        shards = self.hot.get(key)
        if not shards:
            return [(self.storage_owner(key), key)]
        storage_keys = self._shard_storage_keys(key, shards)
        index = self._rr[key] % len(storage_keys)
        self._rr[key] += 1
        storage_key = storage_keys[index]
        return [(self.storage_owner(storage_key), storage_key)]

    def read_locations(self, key: str):
        shards = self.hot.get(key)
        if not shards:
            return [(self.storage_owner(key), key)]
        locations = []
        for storage_key in self._shard_storage_keys(key, shards):
            locations.append((self.storage_owner(storage_key), storage_key))
        return locations

    def _shard_storage_keys(self, key: str, shards: int):
        """First `shards` sub-keys of `key` that land on distinct partitions.
        Deterministic in (key, shards), so writes and reads agree."""
        if shards > self.num_partitions:
            raise ValueError("shards must be <= num_partitions")
        storage_keys = []
        used = set()
        index = 0
        while len(storage_keys) < shards:
            storage_key = shard_storage_key(key, index)
            owner = self.storage_owner(storage_key)
            if owner not in used:
                used.add(owner)
                storage_keys.append(storage_key)
            index += 1
        return storage_keys


# ---------------------------------------------------------------- cluster

class Cluster:
    """Partitioned KV store. Values are (version, value) cells; a read of a
    split key merges shards by max version, giving read-your-writes."""

    def __init__(self, router):
        self.router = router
        self.partitions: list[dict] = [dict() for _ in range(router.num_partitions)]
        self.req_counts = [0] * router.num_partitions
        self.versions: dict[str, int] = defaultdict(int)

    def write(self, key: str, value) -> None:
        partition, storage_key = self.router.write_location(key)[0]
        self.req_counts[partition] += 1
        self.versions[key] += 1
        self.partitions[partition][storage_key] = (self.versions[key], value)

    def read(self, key: str):
        best = None
        for partition, storage_key in self.router.read_locations(key):
            self.req_counts[partition] += 1
            cell = self.partitions[partition].get(storage_key)
            if cell is not None and (best is None or cell[0] > best[0]):
                best = cell
        return best[1] if best is not None else None

    def key_counts(self):
        return [len(p) for p in self.partitions]


# ---------------------------------------------------------------- invariant

def assert_placement_consistent(cluster: Cluster) -> None:
    """Every storage key lives in exactly the partition the routing table
    assigns to it: no key belongs to two partitions, none is homeless."""
    router = cluster.router
    if not isinstance(router, ShardedRouter):
        return
    seen = {}
    for pid, data in enumerate(cluster.partitions):
        for storage_key in data:
            assert storage_key not in seen, (
                f"{storage_key!r} in both partition {seen[storage_key]} and {pid}")
            seen[storage_key] = pid
            owner = router.storage_owner(storage_key)
            assert owner == pid, (
                f"{storage_key!r} served by partition {pid}, table says {owner}")


# ---------------------------------------------------------------- migrations

class BucketMigration:
    """Move one virtual bucket (and its data) to another partition.

    Reentrant: steps are idempotent; recover() re-derives the state from the
    routing table after a crash and the migration converges to DONE.
    """

    PREPARE, FLIP, CLEANUP, DONE = "PREPARE", "FLIP", "CLEANUP", "DONE"

    def __init__(self, cluster: Cluster, bucket: int, dst: int):
        self.cluster = cluster
        self.router = cluster.router
        self.bucket = bucket
        self.src = self.router.bucket_table[bucket]
        self.dst = dst
        if self.src == self.dst:
            raise ValueError("src == dst")
        self.shadow: dict = {}
        self.state = self.PREPARE

    def _bucket_keys(self, partition_dict: dict) -> dict:
        return {k: v for k, v in partition_dict.items()
                if stable_hash(k) % self.router.num_buckets == self.bucket}

    def step(self) -> str:
        if self.state == self.PREPARE:
            self.shadow = self._bucket_keys(self.cluster.partitions[self.src])
            self.state = self.FLIP
        elif self.state == self.FLIP:
            src_part = self.cluster.partitions[self.src]
            dst_part = self.cluster.partitions[self.dst]
            for storage_key, cell in list(self._bucket_keys(src_part).items()):
                dst_part[storage_key] = cell
                del src_part[storage_key]
            for storage_key, cell in self.shadow.items():
                dst_part.setdefault(storage_key, cell)
            self.shadow = {}
            self.router.bucket_table[self.bucket] = self.dst
            self.state = self.CLEANUP
        elif self.state == self.CLEANUP:
            for storage_key in list(self._bucket_keys(self.cluster.partitions[self.src])):
                del self.cluster.partitions[self.src][storage_key]
            self.state = self.DONE
        return self.state

    def recover(self) -> str:
        owner = self.router.bucket_table[self.bucket]
        if owner == self.src:
            self.state = self.PREPARE
        elif owner == self.dst:
            self.state = self.FLIP
        else:
            raise AssertionError(f"bucket {self.bucket} owned by unexpected {owner}")
        return self.state

    def run(self) -> str:
        while self.state != self.DONE:
            self.step()
        return self.state


class SplitMigration:
    """Promote a hot key to S salted shards scattered across partitions."""

    PREPARE, FLIP, CLEANUP, DONE = "PREPARE", "FLIP", "CLEANUP", "DONE"

    def __init__(self, cluster: Cluster, key: str, shards: int):
        if shards < 2:
            raise ValueError("shards must be >= 2")
        if shards > cluster.router.num_partitions:
            raise ValueError("shards must be <= num_partitions")
        self.cluster = cluster
        self.router = cluster.router
        self.key = key
        self.shards = shards
        self.staged = None
        self.state = self.PREPARE

    def step(self) -> str:
        if self.state == self.PREPARE:
            owner = self.router.storage_owner(self.key)
            self.staged = self.cluster.partitions[owner].get(self.key)
            self.state = self.FLIP
        elif self.state == self.FLIP:
            assert self.key not in self.router.hot
            for partition in self.cluster.partitions:
                partition.pop(self.key, None)
            if self.staged is not None:
                for storage_key in self.router._shard_storage_keys(self.key, self.shards):
                    owner = self.router.storage_owner(storage_key)
                    self.cluster.partitions[owner][storage_key] = self.staged
            self.router.hot[self.key] = self.shards
            self.state = self.CLEANUP
        elif self.state == self.CLEANUP:
            for partition in self.cluster.partitions:
                assert self.key not in partition
            self.state = self.DONE
        return self.state

    def recover(self) -> str:
        self.state = self.CLEANUP if self.key in self.router.hot else self.PREPARE
        return self.state

    def run(self) -> str:
        while self.state != self.DONE:
            self.step()
        return self.state


class DemoteMigration:
    """Merge a previously split key back to a single cell (hot spot moved)."""

    PREPARE, FLIP, CLEANUP, DONE = "PREPARE", "FLIP", "CLEANUP", "DONE"

    def __init__(self, cluster: Cluster, key: str):
        self.cluster = cluster
        self.router = cluster.router
        self.key = key
        self.staged = None
        self.state = self.PREPARE

    def step(self) -> str:
        if self.state == self.PREPARE:
            best = None
            prefix = f"{self.key}#shard"
            for partition in self.cluster.partitions:
                for storage_key, cell in partition.items():
                    if storage_key.startswith(prefix):
                        if best is None or cell[0] > best[0]:
                            best = cell
            self.staged = best
            self.state = self.FLIP
        elif self.state == self.FLIP:
            assert self.key in self.router.hot
            prefix = f"{self.key}#shard"
            for partition in self.cluster.partitions:
                for storage_key in [k for k in partition if k.startswith(prefix)]:
                    del partition[storage_key]
            del self.router.hot[self.key]
            if self.staged is not None:
                owner = self.router.storage_owner(self.key)
                self.cluster.partitions[owner][self.key] = self.staged
            self.state = self.CLEANUP
        elif self.state == self.CLEANUP:
            prefix = f"{self.key}#shard"
            for partition in self.cluster.partitions:
                assert not any(k.startswith(prefix) for k in partition)
            self.state = self.DONE
        return self.state

    def recover(self) -> str:
        self.state = self.PREPARE if self.key in self.router.hot else self.CLEANUP
        return self.state

    def run(self) -> str:
        while self.state != self.DONE:
            self.step()
        return self.state


# ---------------------------------------------------------------- detector

class HotKeyDetector:
    """Promotes keys whose window share exceeds promote_factor * fair share;
    demotes split keys whose per-shard share cools below demote_factor."""

    def __init__(self, promote_factor=2.0, demote_factor=0.5,
                 shard_headroom=1.5, max_shards=64):
        self.promote_factor = promote_factor
        self.demote_factor = demote_factor
        self.shard_headroom = shard_headroom
        self.max_shards = max_shards

    def rebalance(self, cluster: Cluster, counts: Counter):
        router = cluster.router
        n = router.num_partitions
        total = sum(counts.values())
        actions = []
        if not total:
            return actions
        fair = total / n
        for key, count in counts.most_common():
            shards = router.hot.get(key)
            if shards is None and count > self.promote_factor * fair:
                # Reads fan out to all S shards, so a split key keeps ~count
                # physical requests per shard-bearing partition. Pick S so
                # that count <= fair physical share: S >= n + 1 - total/count.
                target = math.ceil(self.shard_headroom * (n + 1 - total / count))
                target = max(2, min(self.max_shards, n, target))
                SplitMigration(cluster, key, target).run()
                actions.append(("split", key, target))
            elif shards is not None and count < self.demote_factor * fair:
                DemoteMigration(cluster, key).run()
                actions.append(("merge", key, 1))
        return actions


# ---------------------------------------------------------------- workload

def run_workload(cluster: Cluster, key_stream, detector: HotKeyDetector = None,
                 window: int = 0, read_ratio: float = 0.8, rng=None):
    """Replay a stream of logical keys as reads/writes against the cluster.
    If a detector is given, it rebalances every `window` requests."""
    counts: Counter = Counter()
    ops = 0
    for key in key_stream:
        if rng is not None and rng.random() < read_ratio:
            cluster.read(key)
        else:
            cluster.write(key, ops)
        ops += 1
        if detector is not None:
            counts[key] += 1
            if window and ops % window == 0:
                detector.rebalance(cluster, counts)
                counts.clear()
    if detector is not None and counts:
        detector.rebalance(cluster, counts)
    return ops
