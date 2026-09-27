"""Content-addressed build provenance library (Python 3 stdlib only).

Records, for every build artifact, its complete input set:
  - source files and generated intermediate files (by content fingerprint,
    never by timestamp),
  - the tool identity (name + version) and command-line arguments.

The provenance forms a DAG that can be validated for self-consistency
(every artifact's upstream inputs are resolvable, no orphan nodes) and
replayed (re-hash current inputs to decide whether anything changed).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

FINGERPRINT_ALGO = "sha256"
_CHUNK = 1 << 20


class ProvenanceError(Exception):
    """Raised when recording or validating provenance fails."""


def fingerprint_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint_file(path: str) -> str:
    """Content fingerprint of a file. Raises FileNotFoundError if missing."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class Tool:
    """Build tool identity: name, version and the exact argv used."""
    name: str
    version: str
    argv: Tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def to_dict(self) -> dict:
        return {"name": self.name, "version": self.version, "argv": list(self.argv)}

    @staticmethod
    def from_dict(d: dict) -> "Tool":
        return Tool(d["name"], d["version"], tuple(d.get("argv", ())))


@dataclass
class StepRecord:
    """One build step: a tool invocation consuming inputs, producing outputs."""
    step_id: str
    tool: Tool
    inputs: List[Tuple[str, str]]    # (path, content fingerprint at consume time)
    outputs: List[Tuple[str, str]]   # (path, content fingerprint at produce time)

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "tool": self.tool.to_dict(),
            "inputs": [{"path": p, "fingerprint": f} for p, f in self.inputs],
            "outputs": [{"path": p, "fingerprint": f} for p, f in self.outputs],
        }

    @staticmethod
    def from_dict(d: dict) -> "StepRecord":
        return StepRecord(
            step_id=d["step_id"],
            tool=Tool.from_dict(d["tool"]),
            inputs=[(i["path"], i["fingerprint"]) for i in d["inputs"]],
            outputs=[(o["path"], o["fingerprint"]) for o in d["outputs"]],
        )


class ProvenanceGraph:
    """DAG of sources, generated artifacts and tool invocations."""

    def __init__(self) -> None:
        self.sources: Dict[str, str] = {}          # path -> fingerprint
        self.steps: List[StepRecord] = []
        self._producer: Dict[str, StepRecord] = {}  # output path -> step
        self._consumers: Dict[str, List[StepRecord]] = {}  # input path -> steps
        self.declared_outputs: Set[str] = set()  # build targets declared by the build system

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #
    def add_source(self, path: str, fingerprint: Optional[str] = None) -> str:
        """Register a pre-existing source file by content fingerprint."""
        fp = fingerprint if fingerprint is not None else fingerprint_file(path)
        self.sources[path] = fp
        return fp

    def record_step(
        self,
        step_id: str,
        tool: Tool,
        inputs: Iterable[str],
        outputs: Iterable[str],
        output_fingerprints: Optional[Dict[str, str]] = None,
    ) -> StepRecord:
        """Record a build step.

        Every input must already be a registered source or a previously
        produced artifact (generated files re-consumed downstream).
        Output fingerprints are computed from disk unless supplied.
        """
        inputs = list(inputs)
        outputs = list(outputs)
        in_pairs: List[Tuple[str, str]] = []
        for path in inputs:
            fp = self.known_fingerprint(path)
            if fp is None:
                raise ProvenanceError(
                    f"step {step_id!r}: input {path!r} is neither a registered "
                    f"source nor a previously produced artifact"
                )
            in_pairs.append((path, fp))

        out_pairs: List[Tuple[str, str]] = []
        for path in outputs:
            if path in self._producer:
                raise ProvenanceError(
                    f"step {step_id!r}: output {path!r} already produced by "
                    f"step {self._producer[path].step_id!r}"
                )
            if output_fingerprints and path in output_fingerprints:
                fp = output_fingerprints[path]
            else:
                fp = fingerprint_file(path)
            out_pairs.append((path, fp))

        rec = StepRecord(step_id=step_id, tool=tool, inputs=in_pairs, outputs=out_pairs)
        self.steps.append(rec)
        for path, _ in out_pairs:
            self._producer[path] = rec
        for path, _ in in_pairs:
            self._consumers.setdefault(path, []).append(rec)
        return rec

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def known_fingerprint(self, path: str) -> Optional[str]:
        if path in self.sources:
            return self.sources[path]
        step = self._producer.get(path)
        if step is not None:
            for p, fp in step.outputs:
                if p == path:
                    return fp
        return None

    def artifacts(self) -> List[str]:
        return list(self._producer)

    def final_artifacts(self) -> List[str]:
        """Artifacts not consumed by any later step (DAG sinks)."""
        return [p for p in self._producer if p not in self._consumers]

    def declare_outputs(self, paths: Iterable[str]) -> None:
        """Declare the build's final targets. Validation treats these as
        the DAG roots; produced artifacts that are neither consumed nor
        declared are orphans."""
        for p in paths:
            if p not in self._producer:
                raise ProvenanceError(f"declared output {p!r} was never produced")
            self.declared_outputs.add(p)

    def upstream(self, artifact: str) -> Dict[str, str]:
        """Transitive closure of all upstream inputs of an artifact.

        Returns {path: recorded fingerprint} covering source files and
        generated intermediates. Raises ProvenanceError for unknown artifacts.
        """
        if artifact not in self._producer:
            raise ProvenanceError(f"unknown artifact: {artifact!r}")
        result: Dict[str, str] = {}
        stack = [artifact]
        seen: Set[str] = set()
        while stack:
            path = stack.pop()
            if path in seen:
                continue
            seen.add(path)
            step = self._producer.get(path)
            if step is None:
                continue
            for in_path, in_fp in step.inputs:
                result[in_path] = in_fp
                stack.append(in_path)
        return result

    # ------------------------------------------------------------------ #
    # Validation (graph self-consistency assertions)
    # ------------------------------------------------------------------ #
    def validate(self) -> List[str]:
        """Assert graph self-consistency. Returns a list of violations
        (empty means the graph is valid)."""
        errors: List[str] = []
        nodes: Set[str] = set(self.sources) | set(self._producer)

        # 1. Every step input resolves to a known node.
        for step in self.steps:
            for path, _ in step.inputs:
                if path not in nodes:
                    errors.append(f"step {step.step_id}: dangling input {path!r}")

        # 2. No artifact produced by more than one step.
        produced: Dict[str, str] = {}
        for step in self.steps:
            for path, _ in step.outputs:
                if path in produced:
                    errors.append(
                        f"artifact {path!r} produced by both {produced[path]} "
                        f"and {step.step_id}"
                    )
                produced[path] = step.step_id

        # 3. Acyclicity over file nodes.
        errors.extend(self._cycle_errors())

        # 4. No orphan nodes: every node must be reachable from the declared
        #    build outputs (falling back to DAG sinks when none declared).
        #    Sources never consumed and dead-end intermediates are orphans.
        roots = self.declared_outputs or set(self.final_artifacts())
        reachable: Set[str] = set()
        stack = list(roots)
        while stack:
            path = stack.pop()
            if path in reachable:
                continue
            reachable.add(path)
            step = self._producer.get(path)
            if step is not None:
                stack.extend(p for p, _ in step.inputs)
        for path in nodes - reachable:
            kind = "source" if path in self.sources else "artifact"
            errors.append(f"orphan {kind} node: {path!r} is not upstream of any final artifact")

        # 5. Every artifact can be traced back to at least one source.
        for path in self._producer:
            ups = self.upstream(path)
            if not any(p in self.sources for p in ups):
                errors.append(f"artifact {path!r} has no source-file ancestry")
        return errors

    def _cycle_errors(self) -> List[str]:
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {}
        errors: List[str] = []

        def visit(node: str, trail: List[str]) -> None:
            color[node] = GRAY
            trail.append(node)
            step = self._producer.get(node)
            if step is not None:
                for nxt, _ in step.inputs:
                    if nxt not in self._producer:
                        continue
                    c = color.get(nxt, WHITE)
                    if c == GRAY:
                        cycle = " -> ".join(trail + [nxt])
                        errors.append(f"dependency cycle: {cycle}")
                    elif c == WHITE:
                        visit(nxt, trail)
            trail.pop()
            color[node] = BLACK

        for node in list(self._producer):
            if color.get(node, WHITE) == WHITE:
                visit(node, [])
        return errors

    # ------------------------------------------------------------------ #
    # Replay: decide whether recorded inputs still match the filesystem
    # ------------------------------------------------------------------ #
    def check_replay(
        self,
        artifact: str,
        hasher: Callable[[str], str] = fingerprint_file,
    ) -> "ReplayReport":
        """Re-hash every upstream input of `artifact` and compare with the
        recorded content fingerprints. Pure content comparison: mtime and
        other metadata are ignored."""
        recorded = self.upstream(artifact)
        entries: List[ReplayEntry] = []
        for path in sorted(recorded):
            want = recorded[path]
            try:
                got = hasher(path)
            except FileNotFoundError:
                entries.append(ReplayEntry(path, want, None, ReplayEntry.MISSING))
                continue
            status = ReplayEntry.UNCHANGED if got == want else ReplayEntry.CHANGED
            entries.append(ReplayEntry(path, want, got, status))
        return ReplayReport(artifact=artifact, entries=entries)

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "format": "provbuild/1",
            "fingerprint_algo": FINGERPRINT_ALGO,
            "declared_outputs": sorted(self.declared_outputs),
            "sources": [
                {"path": p, "fingerprint": fp} for p, fp in sorted(self.sources.items())
            ],
            "steps": [s.to_dict() for s in self.steps],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=1, sort_keys=False)

    def save(self, path: str) -> int:
        data = self.to_json().encode("utf-8")
        with open(path, "wb") as fh:
            fh.write(data)
        return len(data)

    @staticmethod
    def from_dict(d: dict) -> "ProvenanceGraph":
        if d.get("format") != "provbuild/1":
            raise ProvenanceError(f"unsupported provenance format: {d.get('format')!r}")
        g = ProvenanceGraph()
        for s in d["sources"]:
            g.add_source(s["path"], fingerprint=s["fingerprint"])
        for sd in d["steps"]:
            rec = StepRecord.from_dict(sd)
            g.steps.append(rec)
            for path, _ in rec.outputs:
                g._producer[path] = rec
            for path, _ in rec.inputs:
                g._consumers.setdefault(path, []).append(rec)
        g.declared_outputs = set(d.get("declared_outputs", []))
        return g

    @staticmethod
    def load(path: str) -> "ProvenanceGraph":
        with open(path, "r", encoding="utf-8") as fh:
            return ProvenanceGraph.from_dict(json.load(fh))


@dataclass
class ReplayEntry:
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    MISSING = "missing"

    path: str
    recorded_fingerprint: str
    current_fingerprint: Optional[str]
    status: str


@dataclass
class ReplayReport:
    artifact: str
    entries: List[ReplayEntry] = field(default_factory=list)

    @property
    def reproducible(self) -> bool:
        """True iff every upstream input still matches its recorded fingerprint."""
        return all(e.status == ReplayEntry.UNCHANGED for e in self.entries)

    def changed(self) -> List[str]:
        return [e.path for e in self.entries if e.status == ReplayEntry.CHANGED]

    def missing(self) -> List[str]:
        return [e.path for e in self.entries if e.status == ReplayEntry.MISSING]

    def summary(self) -> str:
        counts = {ReplayEntry.UNCHANGED: 0, ReplayEntry.CHANGED: 0, ReplayEntry.MISSING: 0}
        for e in self.entries:
            counts[e.status] += 1
        verdict = "REPRODUCIBLE" if self.reproducible else "STALE"
        return (
            f"{self.artifact}: {verdict} "
            f"(inputs={len(self.entries)} unchanged={counts[ReplayEntry.UNCHANGED]} "
            f"changed={counts[ReplayEntry.CHANGED]} missing={counts[ReplayEntry.MISSING]})"
        )
