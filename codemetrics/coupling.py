"""Module-level coupling metrics (Python stdlib only).

For every module inside the analyzed tree we compute:
  Ce (efferent coupling)  - number of *internal* modules this module imports
  Ca (afferent coupling)  - number of *internal* modules that import this one
  instability I = Ce / (Ca + Ce)   (0 = maximally stable, 1 = maximally unstable;
                                    isolated modules report 0.0)

Only imports that resolve to modules inside the analyzed tree are counted;
stdlib/third-party imports are ignored (they do not couple your own modules).
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field


@dataclass
class ModuleCoupling:
    module: str
    path: str
    ce: int
    ca: int
    instability: float
    depends_on: list = field(default_factory=list)   # efferent, sorted
    depended_by: list = field(default_factory=list)  # afferent, sorted


def discover_modules(root):
    """Map dotted module name -> file path for every .py file under root."""
    root = os.path.abspath(root)
    modules = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "__"))]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            rel = os.path.relpath(fpath, root)
            parts = rel[:-3].split(os.sep)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            name = ".".join(parts) if parts else "__init__"
            modules[name] = fpath
    return modules


def _module_parts(modname):
    return modname.split(".") if modname else []


def _resolve_candidate(candidate, known):
    """Map a dotted name to the most specific known internal module."""
    parts = candidate.split(".")
    while parts:
        name = ".".join(parts)
        if name in known:
            return name
        parts.pop()
    return None


def resolve_imports(tree, modname, known):
    """Return the set of internal modules that `modname` depends on."""
    deps = set()
    pkg_parts = _module_parts(modname)
    # A module's own package is its parent path; for pkg/__init__ (name 'pkg')
    # the module *is* the package, so its package is itself.
    is_pkg_init = os.path.basename(known[modname]) == "__init__.py"
    own_pkg = pkg_parts if is_pkg_init else pkg_parts[:-1]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                hit = _resolve_candidate(alias.name, known)
                if hit and hit != modname:
                    deps.add(hit)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(own_pkg)
                for _ in range(node.level - 1):
                    if base:
                        base.pop()
                if node.module:
                    base.extend(node.module.split("."))
                base_name = ".".join(base)
            else:
                base_name = node.module or ""
            # `from pkg import sub` may import a submodule pkg.sub; count the
            # most specific target. If no alias resolves to a module, fall
            # back to the package/module itself.
            hits = set()
            for alias in node.names:
                cand = f"{base_name}.{alias.name}" if base_name else alias.name
                hit = _resolve_candidate(cand, known)
                if hit:
                    hits.add(hit)
            if not hits and base_name:
                hit = _resolve_candidate(base_name, known)
                if hit:
                    hits.add(hit)
            deps.update(h for h in hits if h != modname)
    return deps


def analyze_tree(root):
    """Analyze coupling for all modules under `root` -> {name: ModuleCoupling}."""
    known = discover_modules(root)
    edges = {}  # module -> set of internal deps
    for name, path in known.items():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            edges[name] = resolve_imports(tree, name, known)
        except (SyntaxError, UnicodeDecodeError):
            edges[name] = set()

    depended_by = {name: set() for name in known}
    for src, targets in edges.items():
        for tgt in targets:
            depended_by[tgt].add(src)

    result = {}
    for name, path in sorted(known.items()):
        ce = len(edges[name])
        ca = len(depended_by[name])
        instability = ce / (ca + ce) if (ca + ce) else 0.0
        result[name] = ModuleCoupling(
            module=name,
            path=path,
            ce=ce,
            ca=ca,
            instability=round(instability, 3),
            depends_on=sorted(edges[name]),
            depended_by=sorted(depended_by[name]),
        )
    return result
