"""Planning and execution engine.

plan():            decide which (check, target) pairs must run for a change set
run_full():        run every check on every target
run_incremental(): run only affected targets, reuse baseline cache for the rest
merge_report():    produce a report byte-identical to run_full's

Fallback contract: whenever the affected set cannot be inferred safely, the
plan falls back to a full run. Trigger codes:
  F1 cold cache        no baseline full-run cache exists
  F2 unknown file type a changed path matches no check pattern/config
  F3 unparseable file  a project .py file fails to parse (graph unsafe)
  F4 path escapes root a changed path is outside the repo root
  F5 unknown status    change status not in {A, M, D}
"""

from __future__ import annotations

import ast
import configparser
import hashlib
import json
import os
import sys
import sysconfig
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import __version__
from .checks import Check, CheckContext, default_checks, glob_match
from .depgraph import (DependencyGraph, UnparseableFile, build_graph,
                       module_name_of, resolve_import)
from .model import Change, Finding, Plan, Status, Target

CONFIG_PATH = "checks.conf"
CACHE_DIR = ".inccheck"
CACHE_FILE = "baseline.json"
VALID_STATUS = {"A", "M", "D"}


# ------------------------------------------------------------------ repo view

class Repo:
    """Read-only view of a repository snapshot."""

    def __init__(self, root: str, py_root: str = "src"):
        self.root = os.path.abspath(root)
        self.py_root = py_root

    def read_file(self, rel: str) -> str:
        with open(os.path.join(self.root, *rel.split("/")), "r",
                  encoding="utf-8") as fh:
            return fh.read()

    def exists(self, rel: str) -> bool:
        return os.path.isfile(os.path.join(self.root, *rel.split("/")))

    def list_files(self) -> List[str]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in (".git", CACHE_DIR, "__pycache__"))
            for name in sorted(filenames):
                abs_path = os.path.join(dirpath, name)
                if os.path.islink(abs_path):
                    continue
                out.append(os.path.relpath(abs_path, self.root)
                           .replace(os.sep, "/"))
        return sorted(out)

    def list_py_files(self) -> List[str]:
        prefix = self.py_root + "/"
        return [p for p in self.list_files()
                if p.startswith(prefix) and p.endswith(".py")]

    def load_config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        if self.exists(CONFIG_PATH):
            parser.read_string(self.read_file(CONFIG_PATH))
        return parser

    def build_graph(self) -> DependencyGraph:
        return build_graph(self.list_py_files(), self.py_root, self.root,
                           self.read_file)


# ------------------------------------------------------------------ context

def make_context(repo: Repo) -> CheckContext:
    ctx = CheckContext(repo.root, repo.load_config(), repo.read_file,
                       repo.list_py_files)
    ctx.stdlib_names = frozenset(sys.stdlib_module_names)
    tops = set()
    for path in repo.list_py_files():
        rel = path[len(repo.py_root) + 1:] if repo.py_root else path
        tops.add(rel.split("/")[0].removesuffix(".py"))
    ctx.project_top_names = frozenset(tops)
    ctx.module_of = lambda p: module_name_of(p, repo.py_root)

    def resolve_project(dotted: str) -> bool:
        parts = dotted.split(".")
        base = os.path.join(repo.root, *repo.py_root.split("/"), *parts)
        return (os.path.isfile(base + ".py")
                or os.path.isfile(os.path.join(base, "__init__.py")))

    def module_exports(dotted: str, name: str) -> bool:
        mod = module_name_of(dotted.replace(".", "/") + ".py", repo.py_root)
        path = None
        for cand in repo.list_py_files():
            if module_name_of(cand, repo.py_root) == dotted:
                path = cand
                break
        if path is None:
            return False
        try:
            tree = ast.parse(repo.read_file(path))
        except SyntaxError:
            return False
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == name:
                        return True
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)) and node.name == name:
                return True
            elif isinstance(node, ast.Import):
                if any(a.asname == name or (a.asname is None
                                            and a.name.split(".")[0] == name)
                       for a in node.names):
                    return True
            elif isinstance(node, ast.ImportFrom):
                if any((a.asname or a.name) == name for a in node.names):
                    return True
        return False

    ctx.resolve_project = resolve_project
    ctx.module_exports = module_exports
    return ctx


# ------------------------------------------------------------------ planning

def _known_path(path: str, checks: Sequence[Check], config_paths: Set[str]) -> bool:
    if path in config_paths:
        return True
    return any(c.matches(path) for c in checks)


def _normalize(changes: Sequence[Change]) -> Tuple[List[Change], List[str]]:
    reasons = []
    norm = []
    for ch in changes:
        path = ch.path.replace("\\", "/")
        if path.startswith("./"):
            path = path[2:]
        if ch.status not in VALID_STATUS:
            reasons.append(f"F5: unknown change status '{ch.status}' for {ch.path}")
        if path.startswith("../") or path == ".." or os.path.isabs(ch.path):
            reasons.append(f"F4: path escapes repo root: {ch.path}")
        norm.append(Change(ch.status, path))
    return norm, reasons


def plan(repo: Repo, changes: Sequence[Change],
         checks: Optional[Sequence[Check]] = None,
         cache_exists: bool = True) -> Plan:
    """Map changed files to the set of (check, target) pairs that must run."""
    checks = list(checks or default_checks())
    changes, reasons = _normalize(changes)

    if not cache_exists:
        reasons.append("F1: no baseline cache (cold start)")

    config_paths = {CONFIG_PATH}
    for c in checks:
        config_paths.update(c.config_paths)

    known_files = set(repo.list_files())
    unknown = [c.path for c in changes
               if not _known_path(c.path, checks, config_paths)]
    for path in unknown:
        reasons.append(f"F2: cannot infer impact of '{path}' (no check owns it)")

    deleted_mods = {
        module_name_of(c.path, repo.py_root)
        for c in changes if c.status == "D" and c.path.endswith(".py")
    }
    deleted_mods.discard(None)
    graph: Optional[DependencyGraph] = None
    if any(c.uses_dep_graph for c in checks):
        try:
            graph = build_graph(repo.list_py_files(), repo.py_root, repo.root,
                                repo.read_file, extra_modules=deleted_mods)
        except UnparseableFile as exc:
            reasons.append(
                f"F3: '{exc.path}' does not parse; import graph is unsafe")

    if reasons:
        return Plan(mode="full", fallback_reasons=reasons)

    targets: Set[Target] = set()
    per_change: List[Tuple[Change, List[Target]]] = []

    for ch in changes:
        hit: Set[Target] = set()
        if ch.path in config_paths:
            # Config change: re-run every check that consumes this config.
            for c in checks:
                if ch.path in c.config_paths or ch.path == CONFIG_PATH:
                    if c.is_global:
                        hit.add(Target(c.check_id, None))
                    else:
                        for f in known_files:
                            if c.matches(f):
                                hit.add(Target(c.check_id, f))
        else:
            for c in checks:
                if (c.tree_sensitive and ch.status in ("A", "D")
                        and any(glob_match(p, ch.path)
                                for p in c.watch_patterns)):
                    hit.add(Target(c.check_id, None))
                if not c.matches(ch.path):
                    continue
                if ch.status != "D":
                    hit.add(Target(c.check_id, ch.path))
            # Reverse-dependency fan-out for python modules.
            if graph is not None and ch.path.endswith(".py"):
                mod = module_name_of(ch.path, repo.py_root)
                if mod is not None:
                    for importer in sorted(graph.files_importing(mod)):
                        for c in checks:
                            if c.uses_dep_graph and importer in known_files:
                                hit.add(Target(c.check_id, importer))
        per_change.append((ch, sorted(hit, key=_target_sort_key)))
        targets.update(hit)

    return Plan(mode="incremental",
                targets=sorted(targets, key=_target_sort_key),
                per_change=per_change,
                total_targets=len(all_targets(repo, checks)))


def _target_sort_key(t: Target):
    return (t.check_id, t.path or "")


def all_targets(repo: Repo, checks: Sequence[Check]) -> List[Target]:
    files = repo.list_files()
    out: List[Target] = []
    for c in checks:
        if c.is_global:
            out.append(Target(c.check_id, None))
        else:
            out.extend(Target(c.check_id, f) for f in files if c.matches(f))
    return sorted(out, key=_target_sort_key)


# ------------------------------------------------------------------ cache

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_key(repo: Repo, target: Target, check: Check,
              graph: Optional[DependencyGraph]) -> str:
    """Content hash of everything the check result may depend on."""
    h = hashlib.sha256()
    h.update(("v2:" + __version__).encode())
    h.update(target.check_id.encode())
    for cfg in sorted(set(check.config_paths) | {CONFIG_PATH}):
        h.update(b"\0cfg\0" + cfg.encode())
        h.update(_sha(repo.read_file(cfg)).encode() if repo.exists(cfg) else b"-")
    if target.path is not None:
        h.update(b"\0file\0" + target.path.encode())
        h.update(_sha(repo.read_file(target.path)).encode())
    if check.uses_dep_graph and graph is not None and target.path:
        py_root_abs = os.path.join(repo.root, *repo.py_root.split("/"))
        dep_info = []
        for name in sorted(set(graph.edges.get(target.path, []))):
            resolved = resolve_import(name, py_root_abs)
            if resolved is None:
                dep_info.append([name, None, None])
            else:
                rel = f"{repo.py_root}/{resolved}" if repo.py_root else resolved
                digest = _sha(repo.read_file(rel)) if repo.exists(rel) else None
                dep_info.append([name, rel, digest])
        h.update(b"\0deps\0" + json.dumps(dep_info).encode())
    if check.tree_sensitive:
        listing = "\n".join(repo.list_py_files())
        h.update(b"\0tree\0" + _sha(listing).encode())
    return h.hexdigest()


class Cache:
    def __init__(self, repo: Repo):
        self.dir = os.path.join(repo.root, CACHE_DIR)
        self.path = os.path.join(self.dir, CACHE_FILE)

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def load(self) -> dict:
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, entries: Dict[str, dict]) -> None:
        os.makedirs(self.dir, exist_ok=True)
        payload = {"version": __version__, "entries": entries}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.path)


# ------------------------------------------------------------------ execution

def _execute(repo: Repo, ctx: CheckContext, targets: Sequence[Target],
             checks_by_id: Dict[str, Check]) -> List[Finding]:
    findings = []
    for t in targets:
        findings.append(checks_by_id[t.check_id].run(ctx, t))
    return findings


def _entry(f: Finding, key: str) -> dict:
    return {"status": f.status, "message": f.message, "key": key}


def run_full(repo: Repo, checks: Optional[Sequence[Check]] = None,
             save_cache: bool = True) -> List[Finding]:
    checks = list(checks or default_checks())
    by_id = {c.check_id: c for c in checks}
    ctx = make_context(repo)
    targets = all_targets(repo, checks)
    findings = _execute(repo, ctx, targets, by_id)
    if save_cache:
        try:
            graph = repo.build_graph()
        except UnparseableFile:
            graph = None
        entries = {}
        for t, f in zip(targets, findings):
            entries[f"{t.check_id}|{t.path or ''}"] = _entry(
                f, cache_key(repo, t, by_id[t.check_id], graph))
        Cache(repo).save(entries)
    return findings


@dataclass
class IncrementalResult:
    findings: List[Finding]          # merged, identical to run_full output
    plan: Plan
    ran: List[Target] = field(default_factory=list)
    reused: List[Target] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def run_incremental(repo: Repo, changes: Sequence[Change],
                    checks: Optional[Sequence[Check]] = None,
                    update_cache: bool = True) -> IncrementalResult:
    checks = list(checks or default_checks())
    by_id = {c.check_id: c for c in checks}
    cache = Cache(repo)
    pl = plan(repo, changes, checks, cache_exists=cache.exists())

    if pl.mode == "full":
        findings = run_full(repo, checks, save_cache=update_cache)
        total = len(all_targets(repo, checks))
        metrics = _metrics(pl, ran=total, reused=0, total=total,
                           findings=findings, ran_slots=None, selected=total)
        return IncrementalResult(findings, pl,
                                 ran=all_targets(repo, checks),
                                 metrics=metrics)

    ctx = make_context(repo)
    try:
        graph = repo.build_graph()
    except UnparseableFile:
        graph = None
    baseline = cache.load()["entries"]
    universe = all_targets(repo, checks)

    findings: List[Finding] = []
    ran: List[Target] = []
    reused: List[Target] = []
    new_entries: Dict[str, dict] = {}
    new_failed = 0       # failures not already known from the baseline
    covered_new = 0      # ...of which this run actually re-executed

    for t in universe:
        slot = f"{t.check_id}|{t.path or ''}"
        key = cache_key(repo, t, by_id[t.check_id], graph)
        old = baseline.get(slot)
        if old is None or old["key"] != key:
            f = by_id[t.check_id].run(ctx, t)
            ran.append(t)
            was_ran = True
        else:
            f = Finding(t.check_id, t.path, old["status"], old["message"])
            reused.append(t)
            was_ran = False
        if f.status == Status.FAIL and (old is None
                                        or old["status"] != Status.FAIL):
            new_failed += 1
            if was_ran:
                covered_new += 1
        findings.append(f)
        new_entries[slot] = _entry(f, key)

    if update_cache:
        cache.save(new_entries)

    metrics = _metrics(pl, ran=len(ran), reused=len(reused),
                       total=len(universe), findings=findings,
                       ran_slots={f"{t.check_id}|{t.path or ''}" for t in ran},
                       selected=len(pl.targets),
                       recall=(covered_new / new_failed) if new_failed else 1.0)
    return IncrementalResult(findings, pl, ran=ran, reused=reused,
                             metrics=metrics)


def _metrics(pl: Plan, ran: int, reused: int, total: int,
             findings: List[Finding], ran_slots: Optional[Set[str]],
             selected: int, recall: Optional[float] = None) -> dict:
    failed = [f for f in findings if f.status == Status.FAIL]
    if ran_slots is None:  # full run: every target was executed
        ran_slots = {f"{f.check_id}|{f.target or ''}" for f in findings}
    covered = [f for f in failed
               if f"{f.check_id}|{f.target or ''}" in ran_slots]
    precision = len(covered) / ran if ran else 0.0
    if recall is None:
        recall = 1.0  # full runs execute everything by definition
    return {
        "mode": pl.mode,
        "fallback_reasons": list(pl.fallback_reasons),
        "targets_total": total,
        "targets_selected": selected,
        "targets_ran": ran,
        "targets_reused": reused,
        "work_reduction": (1 - ran / total) if total else 0.0,
        "failed_checks": len(failed),
        "precision": round(precision, 4),   # failed among executed targets
        "recall": round(recall, 4),         # failures caught / all failures
    }


# ------------------------------------------------------------------ report

def render_report(findings: Sequence[Finding], root: str) -> str:
    """Canonical, deterministic report. Incremental merge and full runs
    produce byte-identical output for the same tree."""
    lines = [f"INCCHECK-REPORT v{__version__}", f"ROOT {root}"]
    for f in findings:
        line = f"{f.check_id} {f.target or '<repo>'} {f.status}"
        if f.message:
            line += f" :: {f.message}"
        lines.append(line)
    failed = sum(1 for f in findings if f.status == Status.FAIL)
    lines.append(f"SUMMARY total={len(findings)} failed={failed}")
    return "\n".join(lines) + "\n"


def render_metrics(metrics: dict) -> str:
    lines = []
    for key in ("mode", "targets_total", "targets_selected", "targets_ran",
                "targets_reused",
                "work_reduction", "failed_checks", "precision", "recall"):
        lines.append(f"{key}={metrics[key]}")
    for reason in metrics["fallback_reasons"]:
        lines.append(f"fallback={reason}")
    return "\n".join(lines) + "\n"
