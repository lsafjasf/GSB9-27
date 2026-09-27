"""Field-level data lineage graph keyed by stable identity, not by name.

Every field gets a stable ``field_id`` at creation time. Names, paths and
even the owning entity are mutable attributes; lineage edges only ever
reference ``field_id`` so rename / move / merge never break the chain.

Standard library only.
"""

from __future__ import annotations

import uuid
from collections import deque


class LineageError(Exception):
    """Base class for lineage graph errors."""


class UnknownFieldError(LineageError):
    def __init__(self, field_id, context=""):
        self.field_id = field_id
        msg = f"unknown field id: {field_id!r}"
        if context:
            msg += f" ({context})"
        super().__init__(msg)


class CycleError(LineageError):
    """Raised when the graph contains (or would contain) a cycle."""

    def __init__(self, path):
        self.path = list(path)
        super().__init__("lineage cycle detected: " + " -> ".join(self.path))


class DanglingReferenceError(LineageError):
    """Raised when an edge points at a field that does not exist."""

    def __init__(self, references):
        # references: list of (downstream_id, upstream_id, reason)
        self.references = list(references)
        lines = ["dangling lineage reference(s):"]
        for down, up, reason in self.references:
            lines.append(f"  edge {up!r} -> {down!r}: {reason}")
        super().__init__("\n".join(lines))


class Field:
    """A traceable field. Identity is ``field_id``; everything else is mutable."""

    __slots__ = ("field_id", "name", "entity", "path")

    def __init__(self, field_id, name, entity="", path=""):
        self.field_id = field_id
        self.name = name
        self.entity = entity
        self.path = path

    def __repr__(self):
        return f"Field(id={self.field_id!r}, name={self.name!r}, entity={self.entity!r})"


class LineageGraph:
    def __init__(self):
        self._fields = {}          # field_id -> Field
        self._up = {}              # field_id -> set of direct upstream field_ids
        self._down = {}            # field_id -> set of direct downstream field_ids

    # ------------------------------------------------------------------ fields
    def add_field(self, name, entity="", path="", field_id=None):
        fid = field_id or f"{entity}.{name}.{uuid.uuid4().hex[:12]}"
        if fid in self._fields:
            raise LineageError(f"duplicate field id: {fid!r}")
        self._fields[fid] = Field(fid, name, entity, path)
        self._up[fid] = set()
        self._down[fid] = set()
        return fid

    def get_field(self, field_id):
        try:
            return self._fields[field_id]
        except KeyError:
            raise UnknownFieldError(field_id) from None

    def rename_field(self, field_id, new_name):
        """Rename by stable id: lineage edges are untouched."""
        self.get_field(field_id).name = new_name

    def move_field(self, field_id, new_entity, new_path=None):
        """Move a field to another entity/path: lineage edges are untouched."""
        field = self.get_field(field_id)
        field.entity = new_entity
        if new_path is not None:
            field.path = new_path

    def merge_fields(self, keep_id, drop_id):
        """Merge ``drop_id`` into ``keep_id``; all of drop's edges are re-pointed."""
        if keep_id == drop_id:
            return
        self.get_field(keep_id)
        self.get_field(drop_id)
        for up in list(self._up[drop_id]):
            self.remove_edge(up, drop_id)
            if up != keep_id:
                self.add_edge(up, keep_id)
        for down in list(self._down[drop_id]):
            self.remove_edge(drop_id, down)
            if down != keep_id:
                self.add_edge(keep_id, down)
        self._remove_field(drop_id)

    def delete_field(self, field_id, cascade=False):
        """Delete a field. Refuses to silently sever lineage: if the field
        still has edges, raises DanglingReferenceError unless cascade=True."""
        self.get_field(field_id)
        refs = [(d, field_id, "deleted field still has downstream dependents")
                for d in sorted(self._down[field_id])]
        refs += [(field_id, u, "deleted field still consumes upstream")
                 for u in sorted(self._up[field_id])]
        if refs and not cascade:
            raise DanglingReferenceError(refs)
        for up in list(self._up[field_id]):
            self._down[up].discard(field_id)
        for down in list(self._down[field_id]):
            self._up[down].discard(field_id)
        self._remove_field(field_id)

    def _remove_field(self, field_id):
        del self._fields[field_id]
        del self._up[field_id]
        del self._down[field_id]

    # ------------------------------------------------------------------- edges
    def add_edge(self, upstream_id, downstream_id, check_cycle=True):
        if upstream_id not in self._fields:
            raise UnknownFieldError(upstream_id, f"upstream of {downstream_id!r}")
        if downstream_id not in self._fields:
            raise UnknownFieldError(downstream_id, f"downstream of {upstream_id!r}")
        if upstream_id == downstream_id:
            raise CycleError([upstream_id, downstream_id])
        if check_cycle and self._reachable(upstream_id, downstream_id):
            raise CycleError(self._find_path(upstream_id, downstream_id) + [upstream_id])
        self._up[downstream_id].add(upstream_id)
        self._down[upstream_id].add(downstream_id)

    def _reachable(self, start, target):
        """True if ``target`` is a transitive upstream of ``start``."""
        seen, stack = set(), [start]
        while stack:
            for up in self._up[stack.pop()]:
                if up == target:
                    return True
                if up not in seen:
                    seen.add(up)
                    stack.append(up)
        return False

    def remove_edge(self, upstream_id, downstream_id):
        self._up[downstream_id].discard(upstream_id)
        self._down[upstream_id].discard(downstream_id)

    # ------------------------------------------------------------------ traces
    def upstream(self, field_id):
        """All transitive upstream field_ids reachable from ``field_id``."""
        self.get_field(field_id)
        seen, stack = set(), [field_id]
        while stack:
            for up in self._up[stack.pop()]:
                if up not in seen:
                    seen.add(up)
                    stack.append(up)
        return seen

    def upstream_sources(self, field_id):
        """Transitive upstream fields that have no further inputs (true sources)."""
        return {fid for fid in self.upstream(field_id) if not self._up[fid]}

    def downstream(self, field_id):
        """Full downstream impact set of ``field_id``."""
        self.get_field(field_id)
        seen, stack = set(), [field_id]
        while stack:
            for down in self._down[stack.pop()]:
                if down not in seen:
                    seen.add(down)
                    stack.append(down)
        return seen

    def downstream_impact(self, field_id):
        """Downstream fields with no further dependents (terminal outputs)."""
        return {fid for fid in self.downstream(field_id) if not self._down[fid]}

    # -------------------------------------------------------------- validation
    def validate(self):
        """Whole-graph check: raises CycleError or DanglingReferenceError."""
        dangling = []
        for down, ups in self._up.items():
            if down not in self._fields:
                dangling.append((down, "<edge-target>", "edge target is not a known field"))
            for up in ups:
                if up not in self._fields:
                    dangling.append((down, up, "upstream field does not exist"))
        if dangling:
            raise DanglingReferenceError(dangling)
        self._check_acyclic()

    def _check_acyclic(self):
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {fid: WHITE for fid in self._up}
        for root in self._up:
            if color[root] != WHITE:
                continue
            stack = [(root, iter(self._up[root]))]
            color[root] = GRAY
            path = [root]
            while stack:
                node, it = stack[-1]
                advanced = False
                for nxt in it:
                    if color.get(nxt, BLACK) == GRAY:
                        idx = path.index(nxt)
                        raise CycleError(path[idx:] + [nxt])
                    if color.get(nxt, BLACK) == WHITE:
                        color[nxt] = GRAY
                        path.append(nxt)
                        stack.append((nxt, iter(self._up[nxt])))
                        advanced = True
                        break
                if not advanced:
                    color[node] = BLACK
                    path.pop()
                    stack.pop()

    def _find_path(self, start, end):
        """BFS path start -> ... -> end following upstream edges (for errors)."""
        prev = {start: None}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            if cur == end:
                path = []
                while cur is not None:
                    path.append(cur)
                    cur = prev[cur]
                return path[::-1]
            for nxt in self._up[cur]:
                if nxt not in prev:
                    prev[nxt] = cur
                    queue.append(nxt)
        return [start]

    # --------------------------------------------------------------------- io
    @classmethod
    def from_records(cls, fields, edges):
        """Build from serialized records without eager checks; call
        ``validate()`` afterwards to surface cycles/dangling refs."""
        graph = cls()
        for rec in fields:
            graph.add_field(rec["name"], rec.get("entity", ""),
                            rec.get("path", ""), field_id=rec["field_id"])
        for up, down in edges:
            graph._up.setdefault(down, set()).add(up)
            graph._down.setdefault(up, set()).add(down)
        return graph

    def __len__(self):
        return len(self._fields)
