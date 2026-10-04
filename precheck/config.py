"""Configuration loading and validation.

The dependency map lives in `precheck.json` at the repository root. If that
file is missing or invalid we (a) fall back to the built-in DEFAULT_CONFIG so
that a full run can still execute and (b) surface the error through the
`config-validate` check.
"""

import json
import os

CONFIG_FILE = "precheck.json"

DEFAULT_CONFIG = {
    "version": 1,
    "ignore_patterns": ["**.md", "docs/**", ".git/**"],
    "config_files": ["precheck.json", "DEPENDENCY_MAP.md"],
    "checks": [
        {
            "name": "py-syntax",
            "patterns": ["demo/src/**.py", "demo/tests/**.py"],
        },
        {
            "name": "py-style",
            "patterns": ["demo/src/**.py"],
            "args": {"max_line_length": 100},
        },
        {
            "name": "py-tests",
            "patterns": ["demo/src/**.py", "demo/tests/**.py"],
            "args": {"test_dir": "demo/tests", "pythonpath": ["demo/src"]},
        },
        {
            "name": "json-validate",
            "patterns": ["demo/data/**.json"],
        },
        {
            "name": "config-validate",
            "patterns": ["precheck.json"],
        },
    ],
}




def validate_config(cfg, known_check_names=None, root=None):
    """Return a list of human-readable error strings (empty means valid)."""
    errors = []
    if not isinstance(cfg, dict):
        return ["config must be a JSON object"]
    if cfg.get("version") != 1:
        errors.append("version must be the integer 1")
    for key in ("ignore_patterns", "config_files"):
        value = cfg.get(key)
        if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
            errors.append(f"{key} must be a list of strings")
    checks = cfg.get("checks")
    if not isinstance(checks, list) or not checks:
        errors.append("checks must be a non-empty list")
        checks = []
    seen = set()
    for index, entry in enumerate(checks):
        prefix = f"checks[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{prefix}.name must be a non-empty string")
        elif name in seen:
            errors.append(f"{prefix}.name '{name}' is duplicated")
        elif known_check_names is not None and name not in known_check_names:
            errors.append(f"{prefix}.name '{name}' has no implementation")
        seen.add(name)
        patterns = entry.get("patterns")
        if not isinstance(patterns, list) or not all(isinstance(p, str) and p for p in patterns):
            errors.append(f"{prefix} ('{name}').patterns must be a non-empty list of strings")
    if root is not None and "py-tests" in seen:
        entry = next((c for c in checks if isinstance(c, dict) and c.get("name") == "py-tests"), {})
        test_dir = os.path.join(root, entry.get("args", {}).get("test_dir", ""))
        if not os.path.isdir(test_dir):
            errors.append("py-tests test_dir does not exist on disk")
    return errors


def load_config(root):
    """Return (config, error). error is None when the on-disk config is valid."""
    path = os.path.join(root, CONFIG_FILE)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            cfg = json.load(handle)
    except FileNotFoundError:
        return DEFAULT_CONFIG, f"{CONFIG_FILE} not found; using built-in default dependency map"
    except (OSError, json.JSONDecodeError) as exc:
        return DEFAULT_CONFIG, f"cannot parse {CONFIG_FILE}: {exc}"
    # Import lazily to avoid a circular import (checks -> config).
    from .checks import REGISTRY

    errors = validate_config(cfg, known_check_names=REGISTRY.keys(), root=root)
    if errors:
        return DEFAULT_CONFIG, "invalid " + CONFIG_FILE + ": " + "; ".join(errors)
    return cfg, None
