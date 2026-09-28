"""Causally-consistent version store built on version vectors (standard library only).

Rules used by this module
-------------------------
Let ``V(x)`` be the version vector of version ``x``.

1. Ordering / concurrency:

   * ``x`` happened-before ``y`` (``x -> y``) iff
     ``V(x)[k] <= V(y)[k]`` for every node ``k`` and ``V(x) != V(y)``.
   * ``x`` and ``y`` are concurrent (``x || y``) iff neither vector
     dominates the other: each has at least one coordinate that is strictly
     larger than the other's.
   * Equal vectors are the *same* version (idempotent re-delivery).

2. Causal delivery at a node with local clock ``C`` (messages arriving from
   originator ``i``): a version ``v`` with vector ``V`` is deliverable iff

       V[k] <= C[k]            for every k != i
       V[i] == C[i] + 1        (origin counter is exactly one step ahead)

   Anything not deliverable yet is buffered. Buffering is exactly what makes
   "saw A before B" impossible to observe as "B without A".

3. Reads only expose versions reachable from delivered history (the store is
   causally closed). Multiple delivered heads are returned together and
   flagged ``conflict``; a concurrent head is never silently dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple


class CausalViolation(AssertionError):
    """Raised when an effect is observable without its cause."""


class StaleReplicaError(RuntimeError):
    """Raised when a node cannot satisfy a client's causal-read snapshot."""


# ---------------------------------------------------------------------------
# Version vector
# ---------------------------------------------------------------------------


class VersionVector:
    """Map ``node_id -> counter``; missing coordinates read as 0."""

    __slots__ = ("_v",)

    def __init__(self, values: Optional[Dict[str, int]] = None) -> None:
        self._v: Dict[str, int] = dict(values or {})

    @classmethod
    def from_version(cls, origin: str, counter: int, deps: "VersionVector" = None) -> "VersionVector":
        vv = cls(deps._v if deps is not None else None)
        vv._v[origin] = max(vv._v.get(origin, 0), counter)
        return vv

    def get(self, node: str) -> int:
        return self._v.get(node, 0)

    def merge(self, other: "VersionVector") -> "VersionVector":
        merged = dict(self._v)
        for k, val in other._v.items():
            merged[k] = max(merged.get(k, 0), val)
        return VersionVector(merged)

    def bump(self, node: str) -> "VersionVector":
        merged = dict(self._v)
        merged[node] = merged.get(node, 0) + 1
        return VersionVector(merged)

    def compare(self, other: "VersionVector") -> str:
        """Return one of ``equal``, ``before``, ``after``, ``concurrent``."""
        keys = set(self._v) | set(other._v)
        le = ge = True
        for k in keys:
            a, b = self.get(k), other.get(k)
            if a > b:
                le = False
            if a < b:
                ge = False
        if le and ge:
            return "equal"
        if le:
            return "before"
        if ge:
            return "after"
        return "concurrent"

    def happens_before(self, other: "VersionVector") -> bool:
        return self.compare(other) == "before"

    def is_concurrent(self, other: "VersionVector") -> bool:
        return self.compare(other) == "concurrent"

    def dominates_or_equal(self, other: "VersionVector") -> bool:
        return all(other.get(k) <= self.get(k) for k in set(self._v) | set(other._v))

    def to_dict(self) -> Dict[str, int]:
        return dict(self._v)

    def metadata_bytes(self) -> int:
        return len(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode())

    def copy(self) -> "VersionVector":
        return VersionVector(self._v)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, VersionVector) and self.compare(other) == "equal"

    def __hash__(self) -> int:
        return hash(tuple(sorted(self._v.items())))

    def __repr__(self) -> str:
        return f"VV({self.to_dict()})"


def relation(a: VersionVector, b: VersionVector) -> str:
    """Public helper: causal relationship between two vectors."""
    return a.compare(b)


# ---------------------------------------------------------------------------
# Version + read result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Version:
    key: str
    value: Any
    origin: str
    vector: VersionVector
    parents: Tuple[str, ...] = ()

    @property
    def vid(self) -> str:
        """Stable id derived from the vector coordinates."""
        return f"{self.origin}#{json.dumps(self.vector.to_dict(), sort_keys=True, separators=(',', ':'))}"

    def envelope_bytes(self) -> int:
        """Wire size of metadata attached to one version (no payload)."""
        meta = {
            "origin": self.origin,
            "vector": self.vector.to_dict(),
            "parents": list(self.parents),
        }
        return len(json.dumps(meta, sort_keys=True, separators=(",", ":")).encode())

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "origin": self.origin,
            "vector": self.vector.to_dict(),
            "parents": list(self.parents),
        }


@dataclass(frozen=True)
class ReadResult:
    key: str
    versions: Tuple[Version, ...]
    conflict: bool
    snapshot: VersionVector

    def values(self) -> List[Any]:
        return [v.value for v in self.versions]

    def sample(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "conflict": self.conflict,
            "heads": [v.as_dict() for v in self.versions],
            "snapshot": self.snapshot.to_dict(),
        }


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


@dataclass
class _Delivery:
    version: Version
    status: str  # delivered | duplicate | buffered


class Node:
    """One replica: keeps the delivered clock, versions, and a gap buffer."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self.clock: VersionVector = VersionVector({node_id: 0})
        self._all: Dict[str, Dict[str, Version]] = {}
        self._buffer: List[Version] = []
        self.delivery_log: List[Version] = []

    # -- storage -----------------------------------------------------------

    def _versions_of(self, key: str) -> Dict[str, Version]:
        return self._all.setdefault(key, {})

    def heads(self, key: str) -> List[Version]:
        """Maximal (undominated) delivered versions of a key."""
        versions = list(self._versions_of(key).values())
        result: List[Version] = []
        for v in versions:
            if not any(
                w is not v and v.vector.happens_before(w.vector) for w in versions
            ):
                result.append(v)
        result.sort(key=lambda v: json.dumps(v.vector.to_dict(), sort_keys=True))
        return result

    def read(self, key: str, client_snapshot: Optional[VersionVector] = None) -> ReadResult:
        """Read the key. ``client_snapshot`` enforces causal-read consistency."""
        if client_snapshot is not None and not self.clock.dominates_or_equal(client_snapshot):
            raise StaleReplicaError(
                f"node {self.node_id} clock {self.clock} is behind client "
                f"snapshot {client_snapshot}; serving now could expose a "
                f"state older than one the client already observed"
            )
        heads = self.heads(key)
        return ReadResult(
            key=key,
            versions=tuple(heads),
            conflict=len(heads) > 1,
            snapshot=self.clock.copy(),
        )

    # -- writes ------------------------------------------------------------

    def write(self, key: str, value: Any, parents: Iterable[str] = ()) -> Version:
        """Local write; vector = (clock + parents) bumped at this node."""
        vector = self.clock.bump(self.node_id)
        version = Version(
            key=key,
            value=value,
            origin=self.node_id,
            vector=vector,
            parents=tuple(parents),
        )
        self._apply(version)
        return version

    def merge_heads(self, key: str, value: Any) -> Optional[Version]:
        """Application-level merge of every current head into one new version."""
        current = self.heads(key)
        if not current:
            return None
        parents = tuple(v.vid for v in current)
        if len(current) == 1:
            vector = self.clock.bump(self.node_id)
        else:
            merged = self.clock.copy()
            for v in current:
                merged = merged.merge(v.vector)
            vector = merged.bump(self.node_id)
        version = Version(key, value, self.node_id, vector, parents)
        self._apply(version)
        return version

    # -- delivery ----------------------------------------------------------

    def deliverable(self, version: Version) -> bool:
        vv, origin = version.vector, version.origin
        if any(vv.get(k) > self.clock.get(k) for k in set(vv._v) - {origin}):
            return False
        return vv.get(origin) == self.clock.get(origin) + 1

    def receive(self, version: Version) -> str:
        """Deliver one version now or buffer it until its causes arrive."""
        if version.vid in self._versions_of(version.key):
            return "duplicate"
        if self.deliverable(version):
            self._apply(version)
            self._flush_buffer()
            return "delivered"
        self._buffer.append(version)
        return "buffered"

    def buffered_count(self) -> int:
        return len(self._buffer)

    def _apply(self, version: Version) -> None:
        self.clock = self.clock.merge(version.vector)
        self._versions_of(version.key)[version.vid] = version
        self.delivery_log.append(version)

    def _flush_buffer(self) -> None:
        progress = True
        while progress:
            progress = False
            remaining: List[Version] = []
            for version in self._buffer:
                if version.vid in self._versions_of(version.key):
                    progress = True
                    continue
                if self.deliverable(version):
                    self._apply(version)
                    progress = True
                else:
                    remaining.append(version)
            self._buffer = remaining

    # -- introspection -----------------------------------------------------

    def all_versions(self) -> List[Version]:
        return [v for by_key in self._all.values() for v in by_key.values()]

    def stored_metadata_bytes(self) -> int:
        return sum(v.envelope_bytes() for v in self.all_versions()) + self.clock.metadata_bytes()
