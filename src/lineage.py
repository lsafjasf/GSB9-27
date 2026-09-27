"""Field lineage graph keyed by stable identifiers.

The legacy implementation (see tests/repro_broken_lineage.py) keyed nodes by
field name, so renaming a field silently orphaned every edge that still used
the old name and the lineage chain split in two.

This implementation keys every node by an immutable ``uid``.  Names are just
mutable display attributes, therefore rename / move / merge / delete never
break an edge.

Edge direction: ``add_edge(src, dst)`` means *dst depends on src*, i.e.
src -> dst (upstream flows downstream).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple


class LineageError(Exception):
    """Base class for all lineage errors."""


class UnknownEntityError(LineageError):
    """Raised when a referenced uid does not exist in the graph."""


class DuplicateEntityError(LineageError):
    """Raised when an explicit uid is added twice."""


class DanglingReferenceError(LineageError):
    """Raised when an edge points to a node missing from the graph.

    The offending edge location is available as ``edge`` and ``missing``.
    """

    def __init__(self, edge: Tuple[str, str], missing: str):
        self.edge = edge
        self.missing = missing
        super().__init__(
            "dangling reference: edge %s -> %s points to unknown entity %r"
            % (edge[0], edge[1], missing)
        )


class CycleDetectedError(LineageError):
    """Raised when the graph contains a directed cycle.

    ``cycle`` lists the uids on the closed loop (first uid repeated at the
    end), so the location of the cycle is unambiguous.
    """

    def __init__(self, cycle: List[str], fmt):
        self.cycle = cycle
        names = " -> ".join(fmt(uid) for uid in cycle)
        super().__init__("cycle detected: %s" % names)


@dataclass
class Entity:
    uid: str
    name: str
    kind: str = "field"
    container: Optional[str] = None
    status: str = "active"  # active | tombstoned | merged
    merged_into: Optional[str] = None
    history: List[Tuple[str, str, str]] = field(default_factory=list)

    def label(self) -> str:
        loc = self.container + "." if self.container else ""
        return "%s%s[%s]" % (loc, self.name, self.uid[:8])


class LineageGraph:
    """Directed lineage graph indexed by stable uid."""

    def __init__(self) -> None:
        self._nodes: Dict[str, Entity] = {}
        self._out: Dict[str, Set[str]] = {}
        self._in: Dict[str, Set[str]] = {}

    # ------------------------------------------------------------------ nodes

    def add_entity(
        self,
        name: str,
        uid: Optional[str] = None,
        kind: str = "field",
        container: Optional[str] = None,
    ) -> str:
        uid = uid or uuid.uuid4().hex
        if uid in self._nodes:
            raise DuplicateEntityError("entity %r already exists" % uid)
        self._nodes[uid] = Entity(uid=uid, name=name, kind=kind, container=container)
        self._out[uid] = set()
        self._in[uid] = set()
        return uid

    def get(self, uid: str) -> Entity:
        try:
            return self._nodes[uid]
        except KeyError:
            raise UnknownEntityError("unknown entity %r" % uid) from None

    def exists(self, uid: str) -> bool:
        return uid in self._nodes

    def rename(self, uid: str, new_name: str) -> None:
        """Change the display name. No edge is touched."""
        node = self.get(uid)
        node.history.append(("rename", node.name, new_name))
        node.name = new_name

    def move(self, uid: str, new_container: Optional[str]) -> None:
        """Move an entity to another table/dataset/schema. No edge is touched."""
        node = self.get(uid)
        node.history.append(("move", str(node.container), str(new_container)))
        node.container = new_container

    def find_by_name(self, name: str) -> List[str]:
        """Return uids of *every* entity carrying this name.

        Names are not unique; callers must never assume one name == one entity.
        """
        return [n.uid for n in self._nodes.values() if n.name == name]

    def merge_entities(self, source_uid: str, target_uid: str) -> None:
        """Merge one entity into another, preserving the whole trace chain.

        All edges of the source are rewired to the target, and the source is
        kept as a ``merged`` tombstone so that references recorded before the
        merge (and historical audits) still resolve to it.
        """
        source = self.get(source_uid)
        target = self.get(target_uid)
        if source_uid == target_uid:
            raise LineageError("cannot merge an entity into itself")
        old_upstream = list(self._in[source_uid])
        old_downstream = list(self._out[source_uid])
        for up in old_upstream:
            self._out[up].discard(source_uid)
        for down in old_downstream:
            self._in[down].discard(source_uid)
        self._in[source_uid].clear()
        self._out[source_uid].clear()
        for up in old_upstream:
            self.add_edge(up, target_uid)
        for down in old_downstream:
            self.add_edge(target_uid, down)
        source.status = "merged"
        source.merged_into = target_uid
        source.history.append(("merge", source_uid, target_uid))
        target.history.append(("absorbed", source_uid, target.name))

    def tombstone(self, uid: str, reason: str = "deleted") -> None:
        """Mark an entity deleted while retaining it for lineage queries.

        Deleting the node record itself is what used to create dangling
        references; the graph instead keeps a tombstone and the full chain.
        """
        node = self.get(uid)
        node.status = "tombstoned"
        node.history.append(("delete", node.name, reason))

    def remove_entity(self, uid: str) -> None:
        """Hard delete; refused while the entity still participates in edges."""
        self.get(uid)
        if self._in[uid] or self._out[uid]:
            raise LineageError(
                "cannot delete %r: %d incoming / %d outgoing edges remain; "
                "use tombstone() to retire it without breaking lineage"
                % (uid, len(self._in[uid]), len(self._out[uid]))
            )
        del self._nodes[uid]
        del self._out[uid]
        del self._in[uid]

    @property
    def entities(self) -> Dict[str, Entity]:
        return self._nodes

    # ------------------------------------------------------------------- edges

    def add_edge(self, source_uid: str, target_uid: str) -> None:
        """Record that ``target_uid`` derives from ``source_uid``.

        Both endpoints must exist; adding an edge that closes a cycle raises
        immediately with the full cycle path.
        """
        self.get(source_uid)
        self.get(target_uid)
        if source_uid == target_uid:
            raise CycleDetectedError(
                [source_uid, source_uid], lambda u: self.get(u).label()
            )
        if target_uid in self._out[source_uid]:
            return
        # Incremental cycle check: the new edge closes a cycle only if the
        # target can already reach the source through existing edges.
        cycle = self._find_path(target_uid, source_uid)
        if cycle is not None:
            raise CycleDetectedError(cycle + [target_uid], self._label)
        self._out[source_uid].add(target_uid)
        self._in[target_uid].add(source_uid)

    def immediate_upstream(self, uid: str) -> List[str]:
        return sorted(self._in[self.get(uid).uid])

    def immediate_downstream(self, uid: str) -> List[str]:
        return sorted(self._out[self.get(uid).uid])

    # --------------------------------------------------------------- traversal

    def _resolve(self, uid: str) -> str:
        seen = set()
        cur = uid
        while True:
            node = self._nodes.get(cur)
            if node is None or node.merged_into is None:
                return cur
            if cur in seen:
                raise CycleDetectedError([cur, cur], lambda u: u)
            seen.add(cur)
            cur = node.merged_into

    def upstream(self, uid: str) -> List[str]:
        """All transitive upstream sources, ultimate sources first."""
        uid = self._resolve(self.get(uid).uid)
        discovered, path, pos, done = [], [], {}, set()
        stack: List[Tuple[str, Iterable[str]]] = [(uid, iter(sorted(self._in[uid])))]
        path.append(uid)
        pos[uid] = 0
        while stack:
            node_uid, it = stack[-1]
            advanced = False
            for parent in it:
                if parent in done:
                    continue
                if parent in pos:
                    raise CycleDetectedError(
                        path[pos[parent]:] + [parent], self._label
                    )
                discovered.append(parent)
                pos[parent] = len(path)
                path.append(parent)
                stack.append((parent, iter(sorted(self._in[parent]))))
                advanced = True
                break
            if not advanced:
                path.pop()
                pos.pop(node_uid, None)
                done.add(node_uid)
                stack.pop()
        discovered.reverse()
        return discovered

    def downstream(self, uid: str) -> List[str]:
        """All transitive downstream dependents, nearest dependents first."""
        uid = self._resolve(self.get(uid).uid)
        discovered, path, pos, done = [], [], {}, set()
        stack = [(uid, iter(sorted(self._out[uid])))]
        path.append(uid)
        pos[uid] = 0
        while stack:
            node_uid, it = stack[-1]
            advanced = False
            for child in it:
                if child in done:
                    continue
                if child in pos:
                    raise CycleDetectedError(
                        path[pos[child]:] + [child], self._label
                    )
                discovered.append(child)
                pos[child] = len(path)
                path.append(child)
                stack.append((child, iter(sorted(self._out[child]))))
                advanced = True
                break
            if not advanced:
                path.pop()
                pos.pop(node_uid, None)
                done.add(node_uid)
                stack.pop()
        return discovered

    def _label(self, uid: str) -> str:
        node = self._nodes.get(uid)
        return node.label() if node is not None else "<missing:%s>" % uid

    def _find_path(self, start: str, goal: str) -> Optional[List[str]]:
        """BFS for an existing path start -> goal; None if unreachable."""
        if start == goal:
            return [start]
        parent = {start: None}
        queue = [start]
        head = 0
        while head < len(queue):
            cur = queue[head]
            head += 1
            for nxt in sorted(self._out.get(cur, ())):
                if nxt in parent:
                    continue
                parent[nxt] = cur
                if nxt == goal:
                    path = [goal]
                    while parent[path[-1]] is not None:
                        path.append(parent[path[-1]])
                    path.reverse()
                    return path
                queue.append(nxt)
        return None

    # -------------------------------------------------------------- validation

    def validate(self) -> None:
        """Fail loudly on dangling references or cycles, with locations."""
        for src, targets in self._out.items():
            if src not in self._nodes:
                raise DanglingReferenceError((src, next(iter(targets), "?")), src)
            for dst in targets:
                if dst not in self._nodes:
                    raise DanglingReferenceError((src, dst), dst)
                if src not in self._in.get(dst, ()):
                    raise LineageError(
                        "inconsistent reverse index: %s missing from upstream of %s"
                        % (src, dst)
                    )
        for dst, sources in self._in.items():
            if dst not in self._nodes:
                raise DanglingReferenceError((next(iter(sources), "?"), dst), dst)
            for src in sources:
                if dst not in self._out.get(src, ()):
                    raise LineageError(
                        "inconsistent forward index: %s missing from downstream of %s"
                        % (dst, src)
                    )
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        """Kahn's algorithm; on a cycle, extract one concrete cycle path."""
        indegree = {uid: len(self._in[uid]) for uid in self._nodes}
        queue = [uid for uid, deg in indegree.items() if deg == 0]
        head = 0
        while head < len(queue):
            cur = queue[head]
            head += 1
            for nxt in self._out.get(cur, ()):  # pragma: no branch
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        if head == len(self._nodes):
            return
        cyclic = {uid for uid, deg in indegree.items() if deg > 0}
        start = next(iter(cyclic))
        cycle, cur, seen = [start], start, {start}
        while True:
            nxt = next(uid for uid in self._out[cur] if uid in cyclic)
            if nxt in seen:
                cycle.append(nxt)
                break
            seen.add(nxt)
            cycle.append(nxt)
            cur = nxt
        raise CycleDetectedError(cycle, self._label)

    # ------------------------------------------------------------ persistence

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "entities": [
                {
                    "uid": n.uid,
                    "name": n.name,
                    "kind": n.kind,
                    "container": n.container,
                    "status": n.status,
                    "merged_into": n.merged_into,
                    "history": [list(h) for h in n.history],
                }
                for n in self._nodes.values()
            ],
            "edges": [[s, d] for s, ts in self._out.items() for d in sorted(ts)],
        }

    @classmethod
    def from_dict(cls, data: dict, strict: bool = True) -> "LineageGraph":
        graph = cls()
        for item in data.get("entities", []):
            node = Entity(
                uid=item["uid"],
                name=item["name"],
                kind=item.get("kind", "field"),
                container=item.get("container"),
                status=item.get("status", "active"),
                merged_into=item.get("merged_into"),
            )
            node.history = [tuple(h) for h in item.get("history", [])]
            graph._nodes[node.uid] = node
            graph._out.setdefault(node.uid, set())
            graph._in.setdefault(node.uid, set())
        # Edges are loaded raw so validate() can report dangling endpoints.
        for src, dst in data.get("edges", []):
            graph._out.setdefault(src, set()).add(dst)
            graph._in.setdefault(dst, set()).add(src)
        if strict:
            graph.validate()
        return graph

    # ------------------------------------------------------------------ stats

    def edge_count(self) -> int:
        return sum(len(t) for t in self._out.values())

    def node_count(self) -> int:
        return len(self._nodes)
