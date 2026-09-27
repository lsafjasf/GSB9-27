"""Hardened deserializer.

Security model:
  * Explicit whitelist, default-deny: every type (including alias targets)
    must be registered in the whitelist config; anything else is rejected.
  * Validation is a SEPARATE PASS that completes BEFORE any object is
    constructed, and recursively covers the entire document (typed nodes,
    __args__, __items__, plain dict values, list elements).
  * Every rejection reports the JSON-style path of the offending node.
  * Resource limits: max depth, max node count, max payload bytes.
  * Cyclic references are detected and rejected during validation.
"""

import importlib
import json


class DeserializationError(Exception):
    """Base error for rejected documents."""


class PayloadTooLargeError(DeserializationError):
    pass


class ValidationError(DeserializationError):
    """Rejected during validation. `path` points at the offending node."""

    def __init__(self, path, reason):
        self.path = path or "<root>"
        super().__init__(f"{self.path}: {reason}")


def _resolve(type_path):
    module_name, _, qualname = type_path.rpartition(".")
    module = importlib.import_module(module_name)
    return getattr(module, qualname)


class SecureDeserializer:
    def __init__(self, config):
        if config.get("default_policy", "deny") != "deny":
            raise ValueError("only default_policy='deny' is supported")
        self._types = dict(config.get("types", {}))
        self._aliases = dict(config.get("aliases", {}))
        limits = config.get("limits", {})
        self._max_depth = int(limits.get("max_depth", 64))
        self._max_nodes = int(limits.get("max_nodes", 10000))
        self._max_bytes = int(limits.get("max_bytes", 1 << 20))
        # Aliases may only point at explicitly whitelisted types.
        for alias, target in self._aliases.items():
            if target not in self._types:
                raise ValueError(
                    f"alias {alias!r} points at non-whitelisted type {target!r}"
                )

    @classmethod
    def from_config_file(cls, path):
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    # ------------------------------------------------------------------ API
    def loads(self, payload):
        if len(payload.encode("utf-8")) > self._max_bytes:
            raise PayloadTooLargeError(
                f"payload exceeds limit of {self._max_bytes} bytes"
            )
        doc = json.loads(payload)
        return self.load_obj(doc)

    def load_obj(self, doc):
        # Pass 1: validate the WHOLE document. Nothing is constructed here.
        self._validate(doc, "", set(), 0, [0])
        # Pass 2: construct. Only reachable for fully validated documents.
        return self._construct(doc)

    # ------------------------------------------------------- validation pass
    def _validate(self, node, path, visiting, depth, counter):
        if depth > self._max_depth:
            raise ValidationError(path, f"nesting depth exceeds {self._max_depth}")
        counter[0] += 1
        if counter[0] > self._max_nodes:
            raise ValidationError(path, f"node count exceeds {self._max_nodes}")
        if isinstance(node, list):
            self._validate_container(node, path, visiting, depth, counter)
            for index, item in enumerate(node):
                self._validate(item, f"{path}[{index}]", visiting, depth + 1, counter)
            visiting.discard(id(node))
        elif isinstance(node, dict):
            self._validate_container(node, path, visiting, depth, counter)
            if "__type__" in node:
                self._validate_typed(node, path, visiting, depth, counter)
            else:
                for key, value in node.items():
                    self._validate(value, f"{path}.{key}", visiting, depth + 1, counter)
            visiting.discard(id(node))
        elif node is None or isinstance(node, (bool, int, float, str)):
            return
        else:
            raise ValidationError(
                path, f"unsupported scalar of type {type(node).__name__!r}"
            )

    @staticmethod
    def _validate_container(node, path, visiting, depth, counter):
        node_id = id(node)
        if node_id in visiting:
            raise ValidationError(path, "cyclic reference detected")
        visiting.add(node_id)

    def _validate_typed(self, node, path, visiting, depth, counter):
        raw = node["__type__"]
        if not isinstance(raw, str):
            raise ValidationError(path, "__type__ must be a string")
        # Aliases are resolved BEFORE the whitelist check.
        type_name = self._aliases.get(raw, raw)
        spec = self._types.get(type_name)
        if spec is None:
            raise ValidationError(path, f"type not in whitelist: {type_name!r}")
        ctor = node.get("__constructor__", "__init__")
        allowed_ctors = spec.get("constructors", ["__init__"])
        if ctor not in allowed_ctors:
            raise ValidationError(
                path, f"constructor {ctor!r} not allowed for {type_name!r}"
            )
        for index, arg in enumerate(node.get("__args__", [])):
            self._validate(arg, f"{path}.__args__[{index}]", visiting, depth + 1, counter)
        for index, item in enumerate(node.get("__items__", [])):
            self._validate(item, f"{path}.__items__[{index}]", visiting, depth + 1, counter)

    # ------------------------------------------------------ construction pass
    def _construct(self, node):
        if isinstance(node, list):
            return [self._construct(item) for item in node]
        if isinstance(node, dict):
            if "__type__" in node:
                type_name = self._aliases.get(node["__type__"], node["__type__"])
                cls = _resolve(type_name)
                args = [self._construct(a) for a in node.get("__args__", [])]
                if "__items__" in node:
                    return cls(self._construct(item) for item in node["__items__"])
                ctor = node.get("__constructor__", "__init__")
                if ctor == "__init__":
                    return cls(*args)
                return getattr(cls, ctor)(*args)
            return {key: self._construct(value) for key, value in node.items()}
        return node
