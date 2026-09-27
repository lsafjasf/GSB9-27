"""Content-addressed build provenance: record, validate, replay.

Every build step ("run") is recorded as one JSON line containing:
  - tool name, tool version, command-line arguments
  - the full input set (source files and generated intermediates)
  - the outputs it produced
Files are identified by a SHA-256 content fingerprint, never by mtime,
so "did the input change?" is answered by content, not by the clock.

The trace forms a bipartite DAG of file-nodes and run-nodes.
ProvenanceGraph.validate() asserts the trace is self-consistent:
  - every generated file has exactly one producer run
  - every run input resolves to a source file or a generated file
  - a consumed generated file's fingerprint matches its producer's record
  - the graph is acyclic
  - no orphan nodes: everything is upstream of a declared final artifact

replay_check() walks the full upstream closure of an artifact and
re-fingerprints every input on disk, reporting changed/missing files
and tool-version mismatches.
"""

from __future__ import annotations

import hashlib
import json
import os

SCHEMA_VERSION = 1


class ProvenanceError(Exception):
    pass


def fingerprint_file(path, _chunk_size=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(_chunk_size)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def fingerprint_data(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_entry(path):
    if not os.path.isfile(path):
        raise ProvenanceError(f"file missing at record time: {path}")
    return {"path": path, "sha256": fingerprint_file(path)}


def make_run_record(tool, tool_version, args, inputs, outputs):
    """Build one run record. All inputs/outputs must exist on disk."""
    if not tool or not tool_version:
        raise ProvenanceError("tool and tool_version are required")
    return {
        "schema": SCHEMA_VERSION,
        "type": "run",
        "tool": tool,
        "tool_version": tool_version,
        "args": [str(a) for a in args],
        "inputs": [_file_entry(p) for p in inputs],
        "outputs": [_file_entry(p) for p in outputs],
    }


class ProvenanceRecorder:
    """Appends run records to a JSONL trace file."""

    def __init__(self, trace_path):
        self.trace_path = trace_path
        parent = os.path.dirname(os.path.abspath(trace_path))
        os.makedirs(parent, exist_ok=True)

    def record(self, tool, tool_version, args, inputs, outputs):
        rec = make_run_record(tool, tool_version, args, inputs, outputs)
        with open(self.trace_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        return rec


def load_trace(trace_path):
    records = []
    with open(trace_path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") != "run":
                raise ProvenanceError(f"{trace_path}:{lineno}: unknown record type")
            for key in ("tool", "tool_version", "args", "inputs", "outputs"):
                if key not in rec:
                    raise ProvenanceError(f"{trace_path}:{lineno}: missing key {key!r}")
            records.append(rec)
    return records


class ProvenanceGraph:
    """Bipartite file/run DAG reconstructed from run records."""

    def __init__(self, records):
        self.records = list(records)
        self.producers = {}  # output path -> run index
        self.duplicate_producers = []
        for idx, rec in enumerate(self.records):
            for out in rec["outputs"]:
                if out["path"] in self.producers:
                    self.duplicate_producers.append(out["path"])
                self.producers[out["path"]] = idx

    def producer_of(self, path):
        idx = self.producers.get(path)
        return None if idx is None else self.records[idx]

    def upstream_closure(self, artifact):
        """All runs and files transitively upstream of `artifact`.

        Returns (runs, files) where runs is a list of records and files
        maps path -> {"sha256": ..., "kind": "source"|"generated"}.
        """
        runs = []
        files = {}
        seen_runs = set()
        stack = [artifact]
        while stack:
            path = stack.pop()
            idx = self.producers.get(path)
            if idx is None:
                files.setdefault(path, {"sha256": None, "kind": "source"})
                continue
            if idx in seen_runs:
                continue
            seen_runs.add(idx)
            rec = self.records[idx]
            runs.append(rec)
            for out in rec["outputs"]:
                files[out["path"]] = {"sha256": out["sha256"], "kind": "generated"}
            for inp in rec["inputs"]:
                if inp["path"] not in files:
                    stack.append(inp["path"])
        # second pass: source fingerprints from the consuming run records
        for rec in runs:
            for inp in rec["inputs"]:
                entry = files.get(inp["path"])
                if entry is not None and entry["kind"] == "source" and entry["sha256"] is None:
                    entry["sha256"] = inp["sha256"]
        return runs, files

    def validate(self, final_artifacts, require_sources_on_disk=True):
        """Return a list of structural problems; empty list means the
        trace is self-consistent and free of orphan nodes."""
        errors = []

        for path in self.duplicate_producers:
            errors.append(f"duplicate producer for generated file: {path}")

        produced = set(self.producers)
        for idx, rec in enumerate(self.records):
            for inp in rec["inputs"]:
                path = inp["path"]
                if path in produced:
                    want = self._output_sha(path)
                    if want is not None and want != inp["sha256"]:
                        errors.append(
                            f"run {idx} consumes {path} with fingerprint "
                            f"{inp['sha256'][:12]} but producer recorded {want[:12]}"
                        )
                elif require_sources_on_disk and not os.path.isfile(path):
                    errors.append(f"run {idx} input missing on disk: {path}")

        errors.extend(self._check_acyclic())
        errors.extend(self._check_orphans(final_artifacts))
        return errors

    def _output_sha(self, path):
        rec = self.producer_of(path)
        if rec is None:
            return None
        for out in rec["outputs"]:
            if out["path"] == path:
                return out["sha256"]
        return None

    def _check_acyclic(self):
        # Kahn's algorithm over run nodes; edge runA -> runB when A's
        # output is B's input.
        indeg = {i: 0 for i in range(len(self.records))}
        edges = {i: [] for i in range(len(self.records))}
        for i, rec in enumerate(self.records):
            for inp in rec["inputs"]:
                prod = self.producers.get(inp["path"])
                if prod is not None and prod != i:
                    edges[prod].append(i)
                    indeg[i] += 1
        queue = [i for i, d in indeg.items() if d == 0]
        visited = 0
        while queue:
            node = queue.pop()
            visited += 1
            for nxt in edges[node]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)
        if visited != len(self.records):
            return ["dependency cycle detected among runs"]
        return []

    def _check_orphans(self, final_artifacts):
        reachable_runs = set()
        reachable_files = set()
        stack = list(final_artifacts)
        while stack:
            path = stack.pop()
            if path in reachable_files:
                continue
            reachable_files.add(path)
            idx = self.producers.get(path)
            if idx is None or idx in reachable_runs:
                continue
            reachable_runs.add(idx)
            for inp in self.records[idx]["inputs"]:
                stack.append(inp["path"])
        errors = []
        for i, rec in enumerate(self.records):
            if i not in reachable_runs:
                outs = ", ".join(o["path"] for o in rec["outputs"])
                errors.append(f"orphan run {i} ({rec['tool']} -> {outs}): "
                              f"not upstream of any final artifact")
        for i, rec in enumerate(self.records):
            if i in reachable_runs:
                continue
            for inp in rec["inputs"]:
                if inp["path"] not in reachable_files:
                    errors.append(f"orphan source file: {inp['path']}")
        return errors


def replay_check(records, artifact, tool_versions=None):
    """Decide whether the inputs of `artifact` have changed.

    Walks the full upstream closure and re-fingerprints every file on
    disk. `tool_versions` optionally maps tool name -> current version;
    mismatches against the recorded versions are reported.

    Returns a report dict with status in {"ok", "changed", "missing"}.
    """
    graph = ProvenanceGraph(records)
    if graph.producer_of(artifact) is None and not any(
        inp["path"] == artifact for rec in records for inp in rec["inputs"]
    ):
        raise ProvenanceError(f"unknown artifact (not in trace): {artifact}")

    runs, files = graph.upstream_closure(artifact)
    report = {
        "artifact": artifact,
        "run_count": len(runs),
        "input_count": len(files),
        "changed": [],
        "missing": [],
        "ok_files": 0,
        "tool_mismatches": [],
    }
    for path, meta in sorted(files.items()):
        if not os.path.isfile(path):
            report["missing"].append({"path": path, "kind": meta["kind"]})
            continue
        current = fingerprint_file(path)
        if meta["sha256"] is not None and current != meta["sha256"]:
            report["changed"].append({
                "path": path,
                "kind": meta["kind"],
                "recorded": meta["sha256"],
                "current": current,
            })
        else:
            report["ok_files"] += 1

    if tool_versions:
        seen = set()
        for rec in runs:
            key = (rec["tool"], rec["tool_version"])
            if key in seen:
                continue
            seen.add(key)
            current = tool_versions.get(rec["tool"])
            if current is not None and current != rec["tool_version"]:
                report["tool_mismatches"].append({
                    "tool": rec["tool"],
                    "recorded": rec["tool_version"],
                    "current": current,
                })

    if report["missing"]:
        report["status"] = "missing"
    elif report["changed"] or report["tool_mismatches"]:
        report["status"] = "changed"
    else:
        report["status"] = "ok"
    return report
