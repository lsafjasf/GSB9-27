"""Check registry: every check declares what it depends on.

A check declares:
  - path_patterns: repo-relative globs selecting the files it runs on
                   (empty => the check is global, target path is None)
  - config_paths:  config files whose change must re-run this check
  - uses_dep_graph: needs the project import graph (a change fans out to
                    reverse dependencies)
  - tree_sensitive: result depends on the set of files in the tree, so an
                    add/delete matching watch_patterns re-runs it
"""

from __future__ import annotations

import ast
import configparser
import fnmatch
import io
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .model import Finding, Status, Target


def glob_match(pattern: str, path: str) -> bool:
    """fnmatch with '**' support ('**/' also matches zero directories)."""
    if fnmatch.fnmatchcase(path, pattern):
        return True
    if "**/" in pattern:
        return fnmatch.fnmatchcase(path, pattern.replace("**/", "", 1))
    return False


@dataclass
class Check:
    check_id: str
    name: str
    path_patterns: List[str] = field(default_factory=list)
    config_paths: List[str] = field(default_factory=list)
    uses_dep_graph: bool = False
    tree_sensitive: bool = False
    watch_patterns: List[str] = field(default_factory=list)
    run: Optional[Callable] = None  # run(ctx, target) -> Finding

    def matches(self, path: str) -> bool:
        return any(glob_match(p, path) for p in self.path_patterns)

    @property
    def is_global(self) -> bool:
        return not self.path_patterns


class CheckContext:
    """Everything a check may need, provided by the engine."""

    def __init__(self, root: str, config: configparser.ConfigParser,
                 read_file, list_py_files):
        self.root = root
        self.config = config
        self.read_file = read_file          # read_file(rel_path) -> str
        self.list_py_files = list_py_files  # () -> List[str]
        self.stdlib_names = frozenset()     # top-level stdlib module names
        self.project_top_names = frozenset()  # top-level project packages
        self.module_of = lambda p: None     # rel path -> dotted module name

    def resolve_project(self, dotted: str) -> bool:
        """True when a dotted name points at an existing project file."""
        raise NotImplementedError

    def module_exports(self, dotted: str, name: str) -> bool:
        """True when ``name`` is assigned/imported at module top level."""
        raise NotImplementedError

    def line_limit(self) -> int:
        try:
            return self.config.getint("checks", "line-length", fallback=100)
        except (configparser.Error, ValueError):
            return 100


def _fail(target: Target, message: str) -> Finding:
    return Finding(target.check_id, target.path, Status.FAIL, message)


def _passed(target: Target) -> Finding:
    return Finding(target.check_id, target.path, Status.PASS)


# ---------------------------------------------------------------- C001

def run_syntax(ctx: CheckContext, target: Target) -> Finding:
    try:
        source = ctx.read_file(target.path)
    except OSError as exc:
        return _fail(target, f"unreadable: {exc}")
    try:
        ast.parse(source, filename=target.path)
    except SyntaxError as exc:
        return _fail(target, f"syntax error at line {exc.lineno}: {exc.msg}")
    return _passed(target)


# ---------------------------------------------------------------- C002

def run_imports(ctx: CheckContext, target: Target) -> Finding:
    """Every imported name must resolve to stdlib or a project module."""
    source = ctx.read_file(target.path)
    try:
        tree = ast.parse(source, filename=target.path)
    except SyntaxError:
        return _passed(target)  # C001 owns syntax failures
    missing = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in ctx.stdlib_names:
                    continue
                if top in ctx.project_top_names:
                    # full dotted name must point at a real module
                    if not ctx.resolve_project(alias.name):
                        missing.add(alias.name)
                else:
                    missing.add(top)
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            top = node.module.split(".")[0]
            if top in ctx.stdlib_names:
                continue
            if top not in ctx.project_top_names:
                missing.add(top)
                continue
            # `from shop import inventory` validates shop.inventory; a plain
            # `from shop import x` is allowed only when x is an exported symbol.
            base_exists = ctx.resolve_project(node.module)
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                if ctx.resolve_project(full):
                    continue
                if base_exists and ctx.module_exports(node.module, alias.name):
                    continue
                missing.add(full)
    if missing:
        return _fail(target, "unresolved imports: " + ", ".join(sorted(missing)))
    return _passed(target)


# ---------------------------------------------------------------- C003

def run_line_length(ctx: CheckContext, target: Target) -> Finding:
    limit = ctx.line_limit()
    bad = []
    for lineno, line in enumerate(ctx.read_file(target.path).splitlines(), 1):
        if len(line) > limit:
            bad.append(lineno)
    if bad:
        shown = ",".join(str(n) for n in bad[:5])
        more = f" (+{len(bad) - 5} more)" if len(bad) > 5 else ""
        return _fail(target, f"lines exceed {limit} chars: {shown}{more}")
    return _passed(target)


# ---------------------------------------------------------------- C004

MANIFEST = "MANIFEST.txt"


def run_manifest(ctx: CheckContext, target: Target) -> Finding:
    """MANIFEST.txt must list every project module exactly, nothing more."""
    try:
        raw = ctx.read_file(MANIFEST)
    except OSError:
        return _fail(target, f"{MANIFEST} is missing")
    listed = [ln.strip() for ln in raw.splitlines()
              if ln.strip() and not ln.startswith("#")]
    expected = sorted(
        m for m in (ctx.module_of(p) for p in ctx.list_py_files()) if m
    )
    problems = []
    for name in listed:
        if name not in expected:
            problems.append(f"unknown module '{name}'")
    for name in expected:
        if name not in listed:
            problems.append(f"missing module '{name}'")
    if problems:
        return _fail(target, "; ".join(problems))
    return _passed(target)


# ---------------------------------------------------------------- C005

def run_doc_heading(ctx: CheckContext, target: Target) -> Finding:
    for line in ctx.read_file(target.path).splitlines():
        if line.strip():
            if line.startswith("# "):
                return _passed(target)
            return _fail(target, "first content line must be a '# ' heading")
    return _fail(target, "file is empty")


# ---------------------------------------------------------------- registry

def default_checks() -> List[Check]:
    return [
        Check("C001", "py-syntax", ["src/**/*.py"], ["checks.conf"],
              run=run_syntax),
        Check("C002", "py-imports", ["src/**/*.py"], ["checks.conf"],
              uses_dep_graph=True, run=run_imports),
        Check("C003", "line-length", ["src/**/*.py"], ["checks.conf"],
              run=run_line_length),
        Check("C004", "manifest", [], [MANIFEST], tree_sensitive=True,
              watch_patterns=["src/**/*.py"], run=run_manifest),
        Check("C005", "doc-heading", ["docs/**/*.md"], ["checks.conf"],
              run=run_doc_heading),
    ]
