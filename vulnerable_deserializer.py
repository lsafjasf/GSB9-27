"""INTENTIONALLY VULNERABLE deserializer -- regression baseline only.

This is the pre-fix implementation. It has a "whitelist", but the checks
are shallow and can be bypassed (see bypass_cases.py):

  1. Only the ROOT typed node is checked; nested typed nodes inside
     state/args/lists/dicts are constructed without any check.
  2. Payload-defined "$aliases" are merged over the server config, and
     alias *targets* are never validated against the whitelist.
  3. A payload-supplied "$constructor" dotted path is imported and called
     directly, regardless of the whitelist.
  4. Whitelisted containers (e.g. TypedList) act as a trampoline: their
     items are built recursively with checks disabled.

DO NOT USE. Use secure_deserializer.SecureDeserializer instead.
"""

import importlib
import json


def _import(dotted):
    module_name, _, attr = dotted.rpartition(".")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def loads(text, allowed_types, allowed_aliases=None):
    doc = json.loads(text)
    aliases = dict(allowed_aliases or {})
    if isinstance(doc, dict):
        # FLAW: attacker-controlled aliases override server config.
        aliases.update(doc.get("$aliases", {}))
    return _build(doc, allowed_types, aliases, check=True)


def _build(node, allowed, aliases, check):
    if isinstance(node, list):
        # FLAW: check=False propagates -- nothing below the root is checked.
        return [_build(item, allowed, aliases, False) for item in node]
    if isinstance(node, dict):
        if "$type" in node:
            type_name = node["$type"]
            if type_name in aliases:
                # FLAW: alias target never validated against the whitelist.
                type_name = aliases[type_name]
            elif check and type_name not in allowed:
                raise ValueError(f"type not allowed: {type_name}")
            constructor = node.get("$constructor")
            if constructor:
                # FLAW: arbitrary payload-chosen callable is imported/called.
                cls = _import(constructor)
            else:
                cls = _import(type_name)
            state = {
                key: _build(value, allowed, aliases, False)
                for key, value in node.get("$state", {}).items()
            }
            args = [_build(value, allowed, aliases, False)
                    for value in node.get("$args", [])]
            return cls(*args, **state)
        return {
            key: _build(value, allowed, aliases, False)
            for key, value in node.items()
        }
    return node
