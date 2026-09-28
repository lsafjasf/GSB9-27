"""Cluster harness: network simulation, global causal oracle, and assertions."""

from __future__ import annotations

import random
from typing import Dict, Iterable, List, Optional, Sequence

from causal_store import (
    CausalViolation,
    Node,
    ReadResult,
    Version,
    VersionVector,
    relation,
)


class CausalOracle:
    """Global ground truth over every version ever created.

    ``A`` is a cause of ``B`` iff ``V(A) < V(B)`` componentwise. Equal
    vectors mean the versions are identical; otherwise they are concurrent.
    """

    def __init__(self) -> None:
        self.versions: Dict[str, Version] = {}

    def register(self, version: Version) -> None:
        self.versions[version.vid] = version

    def is_cause(self, cause: Version, effect: Version) -> bool:
        return relation(cause.vector, effect.vector) == "before"

    def causes_of(self, effect: Version) -> List[Version]:
        return [
            v
            for v in self.versions.values()
            if v.key == effect.key and relation(v.vector, effect.vector) == "before"
        ]

    def check_delivery_order(self, delivered: Sequence[Version]) -> None:
        """Assert every delivered effect had all its causes delivered earlier."""
        seen = set()
        for idx, effect in enumerate(delivered):
            missing = [
                cause
                for cause in self.causes_of(effect)
                if cause.vid not in seen
            ]
            if missing:
                labels = ", ".join(
                    f"{c.value}({c.vector.to_dict()})" for c in missing
                )
                raise CausalViolation(
                    f"effect {effect.value}({effect.vector.to_dict()}) "
                    f"delivered at position {idx} without cause(s): {labels}"
                )
            seen.add(effect.vid)


class Cluster:
    def __init__(self, node_ids: Iterable[str], seed: int = 42) -> None:
        self.nodes: Dict[str, Node] = {nid: Node(nid) for nid in node_ids}
        self.oracle = CausalOracle()
        self._rng = random.Random(seed)

    def write(self, node_id: str, key: str, value: str,
              parents: Iterable[str] = ()) -> Version:
        version = self.nodes[node_id].write(key, value, parents)
        self.oracle.register(version)
        return version

    def merge(self, node_id: str, key: str, value: str) -> Version:
        version = self.nodes[node_id].merge_heads(key, value)
        assert version is not None
        self.oracle.register(version)
        return version

    def send(self, src: str, dst: str, versions: Iterable[Version],
             shuffle: bool = False) -> List[str]:
        """Push versions src -> dst, optionally in a shuffled wire order.

        Arrivals that would violate causal order are buffered at the
        destination. The destination's complete delivery log is asserted
        against the global oracle afterwards, so any "effect without cause"
        would fail this test immediately.
        """
        versions = list(versions)
        if shuffle:
            self._rng.shuffle(versions)
        statuses = [self.nodes[dst].receive(v) for v in versions]
        # Validate against the node's whole delivery prefix: causes may have
        # arrived in earlier batches.
        self.oracle.check_delivery_order(self.nodes[dst].delivery_log)
        return statuses

    def send_all(self, src: str, dst: str, shuffle: bool = False) -> List[str]:
        return self.send(
            src, dst, list(self.nodes[src].all_versions()), shuffle=shuffle
        )

    def sync_pair(self, a: str, b: str, shuffle: bool = False) -> None:
        """Bidirectional sync; repeat until both sides' buffers are drained."""
        for _ in range(3):
            self.send_all(a, b, shuffle)
            self.send_all(b, a, shuffle)

    def fully_sync(self, shuffle: bool = False) -> None:
        ids = list(self.nodes)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                self.sync_pair(a, b, shuffle)

    def read(self, node_id: str, key: str,
             client_snapshot: Optional[VersionVector] = None) -> ReadResult:
        return self.nodes[node_id].read(key, client_snapshot)
