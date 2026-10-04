"""Python import dependency graph (project modules only).

Only the standard library is used. Import statements are parsed with ``ast``;
syntax errors are surfaced to the caller because an unparseable project file
makes reverse-dependency inference unsafe and must trigger a full fallback.
"""

from __future__ import annotations

import ast
import os
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set


class UnparseableFile(Exception):
    def __init__(self, path: str, detail: str):
        super().__init__(f"{path}: {detail}")
        self.path = path
        self.detail = detail


def module_name_of(rel_path: str, py_root: str) -> Optional[str]:
    """Return the dotted module name of a .py file, or None if outside root."""
    if not rel_path.endswith(".py"):
        return None
    norm = rel_path.replace("\\", "/")
    root = py_root.strip("/")
    if root:
        prefix = root + "/"
        if not norm.startswith(prefix):
            return None
        norm = norm[len(prefix):]
    if norm.endswith("/__init__.py"):
        return norm[: -len("/__init__.py")].replace("/", ".")
    if norm == "__init__.py":
        return ""
    if norm.endswith(".py"):
        return norm[:-3].replace("/", ".")
    return None


class DependencyGraph:
    """Import graph for project modules.

    ``reverse_names`` maps every module-name prefix that can be imported
    (package or module) to the set of files importing it, so fan-out works
    both for leaf modules and for package ``__init__`` changes.
    """

    def __init__(
        self,
        file_modules: Dict[str, str],
        edges: Dict[str, List[str]],
        reverse_names: Dict[str, Set[str]],
    ):
        self.file_modules = file_modules
        self.edges = edges
        self.reverse_names = reverse_names

    @property
    def module_to_file(self) -> Dict[str, str]:
        return {m: f for f, m in self.file_modules.items()}

    def files_importing(self, module_name: str) -> Set[str]:
        """Files that (transitively) import ``module_name``."""
        result: Set[str] = set(self.reverse_names.get(module_name, ()))
        queue: deque = deque()
        seen: Set[str] = {module_name}
        for importer in list(result):
            imp_mod = self.file_modules.get(importer)
            if imp_mod:
                queue.append(imp_mod)
                seen.add(imp_mod)
        while queue:
            mod = queue.popleft()
            for up in self.reverse_names.get(mod, ()):
                up_mod = self.file_modules.get(up)
                if up and up not in result:
                    result.add(up)
                if up_mod and up_mod not in seen:
                    seen.add(up_mod)
                    queue.append(up_mod)
        return result


def resolve_import(imported: str, py_root_abs: str) -> Optional[str]:
    """Resolve a dotted module name to a project file, or None for non-project.

    The project is restricted to stdlib + project imports, so any unresolved
    top-level name is treated as non-project (never a failure).
    """
    parts = imported.split(".")
    base = os.path.join(py_root_abs, *parts)
    if os.path.isfile(base + ".py"):
        return imported.replace(".", "/") + ".py"
    if os.path.isfile(os.path.join(base, "__init__.py")):
        return imported.replace(".", "/") + "/__init__.py"
    return None


def _relative_base(mod: str, is_package: bool, level: int) -> List[str]:
    """Package parts for a relative import of ``level`` dots in module ``mod``.

    ``is_package`` is True when the module is a package ``__init__``.
    """
    if not mod:
        return []
    parts = mod.split(".")
    package = parts if is_package else parts[:-1]
    if level > 1:
        package = package[: -(level - 1)] if level - 1 <= len(package) else []
    return package


def build_graph(
    py_files: List[str],
    py_root: str,
    root_abs: str,
    read_file,
    extra_modules: Optional[Set[str]] = None,
) -> DependencyGraph:
    """Build the graph for the current tree.

    ``read_file(path)`` returns text. Deleted files must not be included.
    Raises UnparseableFile so the planner can fall back safely.
    """
    py_root_abs = os.path.join(root_abs, *py_root.split("/")) if py_root else root_abs

    file_modules: Dict[str, str] = {}
    edges: Dict[str, List[str]] = defaultdict(list)
    parse_errors: List[UnparseableFile] = []

    for rel in py_files:
        mod = module_name_of(rel, py_root)
        if mod is None:
            continue
        file_modules[rel] = mod
        abs_path = os.path.join(root_abs, *rel.split("/"))
        try:
            tree = ast.parse(read_file(rel), filename=abs_path)
        except SyntaxError as exc:
            parse_errors.append(UnparseableFile(rel, str(exc)))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    edges[rel].append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    is_pkg = rel.endswith("/__init__.py") or rel == "__init__.py"
                    base = _relative_base(mod, is_pkg, node.level)
                    if node.module:
                        base = base + node.module.split(".")
                else:
                    base = node.module.split(".") if node.module else []
                if base:
                    dotted = ".".join(base)
                    edges[rel].append(dotted)
                    # `from shop import pricing` also depends on shop.pricing.
                    for alias in node.names:
                        edges[rel].append(dotted + "." + alias.name)

    if parse_errors:
        raise parse_errors[0]

    known_modules = set(file_modules.values()) | (extra_modules or set())
    reverse_names: Dict[str, Set[str]] = defaultdict(set)
    for src, names in edges.items():
        for name in names:
            # Register when the name resolves now (normal case) or is a
            # module removed in this change set (deletion fan-out).
            target = resolve_import(name, py_root_abs)
            if target is None and name not in known_modules:
                continue
            # Register every package prefix of the imported name so that
            # editing/deleting a package __init__ reaches its importers.
            parts = name.split(".")
            for idx in range(1, len(parts) + 1):
                prefix = ".".join(parts[:idx])
                if prefix in known_modules:
                    reverse_names[prefix].add(src)
    return DependencyGraph(
        file_modules, dict(edges), {k: set(v) for k, v in reverse_names.items()}
    )
