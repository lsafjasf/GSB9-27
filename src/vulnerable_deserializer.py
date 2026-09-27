"""VULNERABLE reference implementation (pre-fix), kept for regression comparison.

Flaws:
  1. The whitelist is only checked on the TOP-LEVEL node; nested nodes are
     constructed without any check.
  2. Aliases are resolved AFTER the whitelist check, so an alias can point
     at a non-whitelisted type.
  3. Any classmethod may be invoked as a "custom constructor".
  4. Container members (__items__) are constructed recursively without
     validation, allowing indirect construction of unauthorized types.
"""

import importlib
import json

WHITELIST = {
    "app_types.Vector",
    "app_types.User",
    "builtins.tuple",
}

# NOTE: 'shell' points at a type that is NOT in WHITELIST.
ALIASES = {
    "vector": "app_types.Vector",
    "user": "app_types.User",
    "shell": "app_types.ShellCommand",
}


def _resolve(type_path):
    module_name, _, qualname = type_path.rpartition(".")
    module = importlib.import_module(module_name)
    return getattr(module, qualname)


def _construct(node):
    if isinstance(node, list):
        return [_construct(item) for item in node]
    if isinstance(node, dict):
        if "__type__" in node:
            # Flaw 2: alias resolved here, AFTER the (top-level-only) check.
            type_name = ALIASES.get(node["__type__"], node["__type__"])
            cls = _resolve(type_name)
            args = [_construct(a) for a in node.get("__args__", [])]
            # Flaw 3: arbitrary classmethod as constructor.
            ctor_name = node.get("__constructor__")
            if ctor_name:
                return getattr(cls, ctor_name)(*args)
            # Flaw 4: container members constructed without validation.
            if "__items__" in node:
                return cls(_construct(item) for item in node["__items__"])
            return cls(*args)
        return {key: _construct(value) for key, value in node.items()}
    return node


def loads(payload):
    doc = json.loads(payload)
    # Flaw 1: only the TOP-LEVEL node is checked against the whitelist.
    if isinstance(doc, dict) and "__type__" in doc:
        type_name = doc["__type__"]
        if type_name not in WHITELIST and type_name not in ALIASES:
            raise ValueError(f"type not allowed: {type_name}")
    return _construct(doc)
