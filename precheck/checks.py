"""Check implementations. Each check enumerates the files matching its
declared dependency patterns and produces a deterministic CheckResult."""

import ast
import io
import json
import os
import sys
import time
import traceback
import unittest
from dataclasses import dataclass, field

from .globmatch import any_match

PASS = "PASS"
FAIL = "FAIL"
ERROR = "ERROR"


@dataclass
class CheckResult:
    name: str
    status: str
    details: list = field(default_factory=list)
    duration_ms: float = 0.0


def expand_files(root, patterns):
    """All files under `root` matching any of the repo-relative patterns."""
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for filename in filenames:
            rel = os.path.relpath(os.path.join(dirpath, filename), root)
            rel = rel.replace(os.sep, "/")
            if any_match(rel, patterns):
                hits.append(rel)
    return sorted(hits)


def py_syntax(root, entry):
    details = []
    for rel in expand_files(root, entry["patterns"]):
        path = os.path.join(root, rel)
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        try:
            ast.parse(source, filename=rel)
        except SyntaxError as exc:
            details.append(f"{rel}:{exc.lineno}: {exc.msg}")
    return PASS if not details else FAIL, details


def py_style(root, entry):
    max_len = int(entry.get("args", {}).get("max_line_length", 100))
    details = []
    for rel in expand_files(root, entry["patterns"]):
        path = os.path.join(root, rel)
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
        lines = content.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if len(line) > max_len:
                details.append(f"{rel}:{lineno}: line too long ({len(line)} > {max_len})")
            if line != line.rstrip():
                details.append(f"{rel}:{lineno}: trailing whitespace")
            if "\t" in line:
                details.append(f"{rel}:{lineno}: tab character")
        if content and not content.endswith("\n"):
            details.append(f"{rel}: file does not end with a newline")
    return PASS if not details else FAIL, details


def py_tests(root, entry):
    args = entry.get("args", {})
    test_dir = os.path.join(root, args.get("test_dir", ""))
    if not os.path.isdir(test_dir):
        return ERROR, [f"test_dir not found: {args.get('test_dir')}"]
    inserted = []
    for extra in args.get("pythonpath", []):
        absolute = os.path.join(root, extra)
        sys.path.insert(0, absolute)
        inserted.append(absolute)
    before = set(sys.modules)
    try:
        suite = unittest.TestLoader().discover(
            start_dir=test_dir, pattern="test_*.py", top_level_dir=test_dir
        )
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    finally:
        for absolute in inserted:
            if absolute in sys.path:
                sys.path.remove(absolute)
        # Purge modules imported from this run so repeated in-process runs
        # against different roots do not see stale imports.
        for name in set(sys.modules) - before:
            sys.modules.pop(name, None)
    details = [f"ran {result.testsRun} test(s)"]
    failures = sorted(result.failures + result.errors, key=lambda item: str(item[0]))
    for test, tb_text in failures:
        last_line = tb_text.strip().splitlines()[-1]
        details.append(f"{test}: {last_line}")
    return (PASS if result.wasSuccessful() else FAIL), details


def json_validate(root, entry):
    details = []
    for rel in expand_files(root, entry["patterns"]):
        path = os.path.join(root, rel)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            details.append(f"{rel}: {exc}")
    return PASS if not details else FAIL, details


def config_validate(root, entry):
    from .config import CONFIG_FILE, validate_config

    rel = entry.get("args", {}).get("file", CONFIG_FILE)
    path = os.path.join(root, rel)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            cfg = json.load(handle)
    except FileNotFoundError:
        return FAIL, [f"{rel}: file not found"]
    except (OSError, json.JSONDecodeError) as exc:
        return FAIL, [f"{rel}: cannot parse: {exc}"]
    errors = validate_config(cfg, known_check_names=REGISTRY.keys(), root=root)
    return (PASS if not errors else FAIL), [f"{rel}: {e}" for e in errors]


REGISTRY = {
    "py-syntax": py_syntax,
    "py-style": py_style,
    "py-tests": py_tests,
    "json-validate": json_validate,
    "config-validate": config_validate,
}


def run_check(root, entry):
    name = entry["name"]
    func = REGISTRY[name]
    started = time.perf_counter()
    try:
        status, details = func(root, entry)
    except Exception:  # a crashing check is an ERROR, never a silent pass
        status = ERROR
        details = [traceback.format_exc().strip().splitlines()[-1]]
    duration_ms = (time.perf_counter() - started) * 1000.0
    return CheckResult(name=name, status=status, details=details, duration_ms=duration_ms)
