"""Partitioning schemes, skew metrics, and reentrant single-record migration.

Stdlib only (Python 3).

Schemes
-------
HashPartitioner      : baseline, `md5(key) % N`.  Reproduces the hotspot:
                       one extremely popular key pins a single partition.
BalancedPartitioner  : consistent-hash ring (vnodes) + automatic hot-key
                       splitting (salting) with scatter/gather compensation.

Semantics
---------
* Normal keys: same key -> same partition, stable across restarts.
* Hot keys   : the same-key->same-partition guarantee is deliberately
               broken.  Compensation: writes round-robin over K salted
               shard keys spread across the ring; reads fan out to all K
               shards and merge (counters: sum), so read-your-writes and
               exact totals are preserved at the router level.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
from collections import Counter


def stable_hash(key: str) -> int:
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)


# --------------------------------------------------------------------- metrics

def distribution_metrics(loads):
    """Quantify skew of per-partition loads (key counts or request volumes)."""
    vals = sorted(float(x) for x in loads)
    n = len(vals)
    total = sum(vals)
    mean = total / n if n else 0.0
    median = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
    p99 = vals[max(0, min(n - 1, math.ceil(0.99 * n) - 1))] if n else 0.0
    var = sum((x - mean) ** 2 for x in vals) / n if n else 0.0
    std = math.sqrt(var)
    sq = sum(x * x for x in vals)
    jain = (total * total) / (n * sq) if sq > 0 else 1.0
    top = vals[-1] if vals else 0.0
    return {
        "min": vals[0] if vals else 0.0,
        "median": median,
        "mean": mean,
        "p99": p99,
        "max": top,
        "max_over_median": (top / median) if median > 0 else (math.inf if top > 0 else 1.0),
        "max_over_mean": (top / mean) if mean > 0 else 1.0,
        "cv": (std / mean) if mean > 0 else 0.0,          # coefficient of variation
        "jain": jain,                                      # 1.0 == perfectly fair
        "top_share": (top / total) if total > 0 else 0.0,  # busiest partition's share
        "active": sum(1 for x in vals if x > 0),           # partitions carrying load
    }


# --------------------------------------------------------------------- store

class KVStore:
    """One dict per partition; values are integer counters."""

    def __init__(self, num_partitions: int):
        self.parts = [dict() for _ in range(num_partitions)]

    def get(self, p, k):
        return self.parts[p].get(k, 0)

    def set(self, p, k, v):
        self.parts[p][k] = v

    def add(self, p, k, delta):
        self.parts[p][k] = self.parts[p].get(k, 0) + delta

    def delete(self, p, k):
        self.parts[p].pop(k, None)

    def locations(self, k):
        return [p for p in range(len(self.parts)) if k in self.parts[p]]


# --------------------------------------------------------------------- baseline

class HashPartitioner:
    """Baseline: hash(key) % N.  One hot key saturates one partition."""

    def __init__(self, num_partitions, store=None):
        self.n = num_partitions
        self.store = store or KVStore(num_partitions)

    def write_key(self, key, delta=1):
        p = stable_hash(key) % self.n
        self.store.add(p, key, delta)
        return [(p, key)]

    def read_key(self, key):
        p = stable_hash(key) % self.n
        return self.store.get(p, key), [(p, key)]

    def observe(self, key):  # no hot-key bookkeeping
        pass


# --------------------------------------------------------------------- migration

class MigrationError(Exception):
    pass


class MigrationManager:
    """Reentrant single-record migration with a single-owner invariant.

    Protocol: COPY -> CUTOVER -> CLEANUP -> DONE.

    The journal is the single source of truth for ownership and always
    names exactly ONE owner partition for the in-flight record, so a record
    never *belongs to* two partitions at once.  During COPY a shadow copy
    exists at the destination but is not owned/served until the atomic
    CUTOVER flip.  `begin()` is idempotent (re-issuing the same migration
    is a no-op) and the journal can be persisted to disk so a crashed
    process resumes exactly where it stopped.
    """

    STATES = ("COPY", "CUTOVER", "CLEANUP", "DONE")

    def __init__(self, store: KVStore, journal_path=None):
        self.store = store
        self.journal_path = journal_path
        self.journal = self._load_journal()

    # -- journal persistence (crash recovery / reentrancy) --
    def _load_journal(self):
        if self.journal_path and os.path.exists(self.journal_path):
            with open(self.journal_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        return None

    def _persist(self):
        if not self.journal_path:
            return
        if self.journal is None:
            if os.path.exists(self.journal_path):
                os.remove(self.journal_path)
            return
        tmp = self.journal_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.journal, fh)
        os.replace(tmp, self.journal_path)

    # -- public API --
    def begin(self, src_key, dst_key, src_partition, dst_partition):
        """Idempotent: re-issuing the identical migration returns current state."""
        if (src_key, src_partition) == (dst_key, dst_partition):
            raise MigrationError("source and destination are identical")
        if self.journal is not None:
            j = self.journal
            same = (j["src_key"], j["dst_key"], j["src_partition"], j["dst_partition"]) == \
                   (src_key, dst_key, src_partition, dst_partition)
            if same:
                return j["state"]
            raise MigrationError("another migration is already in flight")
        if src_key not in self.store.parts[src_partition]:
            raise MigrationError(f"source record {src_key!r} not at partition {src_partition}")
        if dst_key in self.store.parts[dst_partition]:
            raise MigrationError(f"destination record {dst_key!r} already exists")
        self.journal = {
            "src_key": src_key, "dst_key": dst_key,
            "src_partition": src_partition, "dst_partition": dst_partition,
            "value": self.store.get(src_partition, src_key),
            "owner": src_partition,
            "owner_key": src_key,
            "state": "COPY",
        }
        self._persist()
        self.assert_invariants()
        return "COPY"

    @property
    def in_flight(self):
        return self.journal is not None

    def owner_of(self, key):
        """The single partition that currently owns/serves `key` (or None)."""
        if self.journal and key in (self.journal["src_key"], self.journal["dst_key"]):
            return self.journal["owner"]
        locs = self.store.locations(key)
        assert len(locs) <= 1, f"{key!r} owned by multiple partitions: {locs}"
        return locs[0] if locs else None

    def step(self):
        """Advance the protocol by one atomic step; returns the new state."""
        j = self.journal
        if j is None:
            return "DONE"
        state = j["state"]
        if state == "COPY":
            # Idempotent set (safe to re-run after a crash).  Destination
            # copy is a shadow: ownership stays with the source.
            self.store.set(j["dst_partition"], j["dst_key"], j["value"])
            j["state"] = "CUTOVER"
        elif state == "CUTOVER":
            # atomic ownership flip (explicit key: src/dst partitions may
            # coincide when a migration only renames within a partition)
            j["owner"] = j["dst_partition"]
            j["owner_key"] = j["dst_key"]
            j["state"] = "CLEANUP"
        elif state == "CLEANUP":
            self.store.delete(j["src_partition"], j["src_key"])
            j["state"] = "DONE"
        elif state == "DONE":
            assert self.store.locations(j["dst_key"]) == [j["dst_partition"]]
            assert self.store.locations(j["src_key"]) == []
            self.journal = None
            self._persist()
            return "DONE"
        self._persist()
        self.assert_invariants()
        return j["state"]

    def run_to_completion(self, on_step=None):
        while self.journal is not None:
            self.step()
            if on_step:
                on_step(self)

    # -- invariant --
    def assert_invariants(self):
        j = self.journal
        if j is None:
            return
        # 1. Exactly one owner at every observable point.
        assert j["owner"] in (j["src_partition"], j["dst_partition"])
        # 2. The record is never lost: full value lives at the owner.
        assert self.store.get(j["owner"], j["owner_key"]) == j["value"], \
            "data lost or corrupted during migration"
        # 3. No third partition ever holds either key.
        for p in range(len(self.store.parts)):
            if p in (j["src_partition"], j["dst_partition"]):
                continue
            assert j["src_key"] not in self.store.parts[p]
            assert j["dst_key"] not in self.store.parts[p]


# --------------------------------------------------------------------- fixed scheme

class BalancedPartitioner:
    """Consistent-hash ring (vnodes) + automatic hot-key splitting.

    Same-key->same-partition holds for normal keys.  Hot keys are split
    into `shards` salted sub-keys; the broken guarantee is compensated by
    scatter/gather in write_key()/read_key() (see module docstring).
    """

    def __init__(self, num_partitions, store=None, vnodes=64, shards=16,
                 hot_factor=2.0, window=1000):
        self.n = num_partitions
        self.store = store or KVStore(num_partitions)
        self.shards = max(1, min(shards, num_partitions))
        self.hot_factor = hot_factor
        self.window = window
        self.hot = {}            # key -> shard count
        self.rr = Counter()      # key -> round-robin cursor
        self.counts = Counter()  # current detection window
        self.seen = 0
        ring = []
        for p in range(num_partitions):
            for i in range(vnodes):
                ring.append((stable_hash(f"vnode:{p}:{i}"), p))
        ring.sort()
        self._ring = ring
        self._ring_hashes = [h for h, _ in ring]

    # -- routing --
    def _ring_partition(self, key):
        h = stable_hash(key)
        idx = bisect.bisect(self._ring_hashes, h) % len(self._ring_hashes)
        return self._ring[idx][1]

    @staticmethod
    def _shard_key(key, i):
        return f"{key}#shard{i}"

    def write_key(self, key, delta=1):
        if key in self.hot:
            i = self.rr[key] % self.hot[key]
            self.rr[key] += 1
            sk = self._shard_key(key, i)
            p = self._ring_partition(sk)
            self.store.add(p, sk, delta)
            return [(p, sk)]
        p = self._ring_partition(key)
        self.store.add(p, key, delta)
        return [(p, key)]

    def read_key(self, key):
        if key in self.hot:
            total, touched = 0, []
            for i in range(self.hot[key]):
                sk = self._shard_key(key, i)
                p = self._ring_partition(sk)
                total += self.store.get(p, sk)
                touched.append((p, sk))
            return total, touched
        p = self._ring_partition(key)
        return self.store.get(p, key), [(p, key)]

    # -- hot-key detection (sliding window) --
    def observe(self, key):
        self.counts[key] += 1
        self.seen += 1
        if self.seen >= self.window:
            self.reclassify()
            self.counts.clear()
            self.seen = 0

    def reclassify(self):
        total = sum(self.counts.values())
        if total == 0:
            return
        # A key is hot when it alone exceeds hot_factor x a partition's
        # fair share of the window's traffic.
        threshold = self.hot_factor * total / self.n
        new_hot = {k for k, c in self.counts.items() if c > threshold}
        for k in sorted(new_hot - set(self.hot)):
            self._split(k)
        for k in sorted(set(self.hot) - new_hot):
            self._unsplit(k)

    # -- split / unsplit (data moves go through the reentrant migrator) --
    def _split(self, key):
        self.hot[key] = self.shards
        p = self._ring_partition(key)
        if key not in self.store.parts[p]:
            return  # nothing to move yet; routing change only
        mig = MigrationManager(self.store)
        mig.begin(key, self._shard_key(key, 0), p, self._ring_partition(self._shard_key(key, 0)))
        mig.run_to_completion()

    def _unsplit(self, key):
        k = self.hot.pop(key)
        self.rr.pop(key, None)
        sk0 = self._shard_key(key, 0)
        p0 = self._ring_partition(sk0)
        for i in range(1, k):  # merge shards 1..k-1 into shard 0
            sk = self._shard_key(key, i)
            p = self._ring_partition(sk)
            if sk in self.store.parts[p]:
                self.store.add(p0, sk0, self.store.get(p, sk))
                self.store.delete(p, sk)
        if sk0 in self.store.parts[p0]:  # move merged record back to natural key
            mig = MigrationManager(self.store)
            mig.begin(sk0, key, p0, self._ring_partition(key))
            mig.run_to_completion()
