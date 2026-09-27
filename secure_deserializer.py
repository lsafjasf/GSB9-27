"""Secure JSON-based object deserializer with an explicit whitelist.

Design (the fix for vulnerable_deserializer.py):

  * Two phases. Phase 1 validates the ENTIRE decoded payload recursively
    (every nested typed node, alias resolution, constructor policy, field
    policy, $id/$ref graph, depth/node/byte limits) BEFORE any object is
    constructed. Phase 2 constructs only what phase 1 approved.
  * Default deny. Only types explicitly registered in the server-side
    whitelist can be built. Aliases live only in the whitelist and their
    targets must themselves be whitelisted. Payloads cannot define
    aliases ($aliases) or pick constructors ($constructor); typed nodes
    may only contain $type/$state/$args/$id.
  * Constructors come from the whitelist registration (a classmethod
    name on the registered class), never from the payload.
  * Errors carry a JSON-path (e.g. "$.$state.items[1].$type") pointing
    at the offending node.

Payload format:
  typed node : {"$type": "<dotted.Class|alias>", "$state": {...},
                "$args": [...], "$id": "name"}
  reference  : {"$ref": "name"}   (must reference a previously defined,
               fully-completed $id; cycles and forward refs are rejected)
  everything else is plain JSON data (lists/dicts may nest typed nodes).
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

RESERVED_PREFIX = "$"
TYPED_NODE_KEYS = frozenset({"$type", "$state", "$args", "$id"})


class DeserializationError(Exception):
    """Raised when a payload violates the deserialization policy."""

    def __init__(self, message: str, path: str = "$"):
        self.path = path
        super().__init__(f"{message} (at {path})")


def _join(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    if isinstance(key, str) and (
            key.isidentifier()
            or (key.startswith("$") and key[1:].isidentifier())):
        return f"{path}.{key}"
    return f"{path}[{key!r}]"


def import_symbol(dotted: str) -> Any:
    module_name, _, attr = dotted.rpartition(".")
    if not module_name or not attr:
        raise DeserializationError(f"invalid dotted path: {dotted!r}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise DeserializationError(
            f"cannot import module for {dotted!r}: {exc}") from None
    try:
        return getattr(module, attr)
    except AttributeError:
        raise DeserializationError(
            f"module {module_name!r} has no attribute {attr!r}") from None


_KIND_CHECKS = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "any": lambda v: True,
}


@dataclass(frozen=True)
class TypeRule:
    """Whitelist entry for one allowed type.

    constructor: optional classmethod name on the registered class used
                 to build the instance (e.g. "from_dict"). Never taken
                 from the payload.
    fields:      optional mapping of allowed $state field -> kind
                 ("str" | "int" | "number" | "bool" | "any").
    """

    name: str
    constructor: Optional[str] = None
    fields: Optional[Dict[str, str]] = None

    def __post_init__(self):
        if self.constructor is not None and not self.constructor.isidentifier():
            raise ValueError(
                f"constructor for {self.name!r} must be a simple attribute "
                f"name of the registered class, got {self.constructor!r}")
        if self.fields is not None:
            for field_name, kind in self.fields.items():
                if kind not in _KIND_CHECKS:
                    raise ValueError(
                        f"unknown kind {kind!r} for field "
                        f"{self.name}.{field_name}")


class Whitelist:
    """Explicit allow-list. Anything not registered here is denied."""

    def __init__(self, types: Dict[str, TypeRule], aliases: Dict[str, str]):
        self.types = dict(types)
        self.aliases = dict(aliases)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Whitelist":
        types = {}
        for name, rule in (data.get("types") or {}).items():
            types[name] = TypeRule(
                name=name,
                constructor=rule.get("constructor"),
                fields=rule.get("fields"),
            )
        return cls(types, dict(data.get("aliases") or {}))

    @classmethod
    def from_json_file(cls, path: str) -> "Whitelist":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def resolve(self, type_name: str, path: str) -> TypeRule:
        """Resolve a payload $type (alias or full name) to a TypeRule.

        Default deny: unknown names and aliases pointing at unregistered
        targets are rejected.
        """
        if type_name in self.aliases:
            target = self.aliases[type_name]
            rule = self.types.get(target)
            if rule is None:
                raise DeserializationError(
                    f"alias {type_name!r} resolves to non-whitelisted "
                    f"type {target!r}", path)
            return rule
        rule = self.types.get(type_name)
        if rule is None:
            raise DeserializationError(
                f"type not whitelisted: {type_name!r}", path)
        return rule


class SecureDeserializer:
    def __init__(self, whitelist: Whitelist, *, max_depth: int = 64,
                 max_nodes: int = 100_000, max_bytes: int = 1_000_000):
        self.whitelist = whitelist
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_bytes = max_bytes

    # ------------------------------------------------------------------
    # public entry point
    # ------------------------------------------------------------------
    def loads(self, text: str) -> Any:
        raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        if len(raw) > self.max_bytes:
            raise DeserializationError(
                f"payload of {len(raw)} bytes exceeds max_bytes "
                f"({self.max_bytes})", "$")
        try:
            doc = json.loads(text)
        except RecursionError:
            raise DeserializationError(
                "payload nesting too deep to parse safely", "$") from None
        except json.JSONDecodeError as exc:
            raise DeserializationError(f"invalid JSON: {exc}", "$") from None

        # Phase 1: validate everything, construct nothing.
        self._node_count = 0
        self._ids: Dict[str, str] = {}
        self._validate(doc, "$", 0)

        # Phase 2: construct only what phase 1 approved.
        self._memo: Dict[str, Any] = {}
        return self._build(doc)

    # ------------------------------------------------------------------
    # phase 1: validation (no objects are constructed here)
    # ------------------------------------------------------------------
    def _validate(self, node: Any, path: str, depth: int) -> None:
        if depth > self.max_depth:
            raise DeserializationError(
                f"nesting exceeds max_depth ({self.max_depth})", path)
        self._node_count += 1
        if self._node_count > self.max_nodes:
            raise DeserializationError(
                f"structure exceeds max_nodes ({self.max_nodes})", path)

        if isinstance(node, list):
            for index, item in enumerate(node):
                self._validate(item, _join(path, index), depth + 1)
        elif isinstance(node, dict):
            if "$ref" in node:
                self._validate_ref(node, path)
            elif "$type" in node:
                self._validate_typed(node, path, depth)
            else:
                for key, value in node.items():
                    if isinstance(key, str) and key.startswith(RESERVED_PREFIX):
                        raise DeserializationError(
                            f"reserved key {key!r} in plain object",
                            _join(path, key))
                    self._validate(value, _join(path, key), depth + 1)

    def _validate_ref(self, node: dict, path: str) -> None:
        if set(node) != {"$ref"}:
            raise DeserializationError(
                "$ref node must not have sibling keys", path)
        ref = node["$ref"]
        if not isinstance(ref, str):
            raise DeserializationError("$ref must be a string id",
                                       _join(path, "$ref"))
        status = self._ids.get(ref)
        if status is None:
            raise DeserializationError(
                f"unknown $ref id {ref!r} "
                f"(forward references are not allowed)", _join(path, "$ref"))
        if status == "in_progress":
            raise DeserializationError(
                f"circular reference via $ref id {ref!r}", _join(path, "$ref"))

    def _validate_typed(self, node: dict, path: str, depth: int) -> None:
        extra = set(node) - TYPED_NODE_KEYS
        if extra:
            raise DeserializationError(
                f"forbidden keys in typed node: {sorted(extra)} "
                f"(payloads cannot define constructors or aliases)", path)
        type_name = node["$type"]
        if not isinstance(type_name, str):
            raise DeserializationError("$type must be a string",
                                       _join(path, "$type"))
        rule = self.whitelist.resolve(type_name, _join(path, "$type"))

        state = node.get("$state", {})
        args = node.get("$args")
        if not isinstance(state, dict):
            raise DeserializationError("$state must be an object",
                                       _join(path, "$state"))
        if args is not None and not isinstance(args, list):
            raise DeserializationError("$args must be an array",
                                       _join(path, "$args"))

        if rule.fields is not None:
            unknown = sorted(set(state) - set(rule.fields))
            if unknown:
                raise DeserializationError(
                    f"fields not allowed for {rule.name}: {unknown}",
                    _join(path, "$state"))
            for key, value in state.items():
                if isinstance(value, dict) and ("$type" in value or "$ref" in value):
                    continue  # nested typed/ref node, validated recursively
                kind = rule.fields.get(key, "any")
                if not _KIND_CHECKS[kind](value):
                    raise DeserializationError(
                        f"field {key!r} of {rule.name} expects {kind}",
                        _join(_join(path, "$state"), key))

        node_id = node.get("$id")
        if node_id is not None:
            if not isinstance(node_id, str):
                raise DeserializationError("$id must be a string",
                                           _join(path, "$id"))
            if node_id in self._ids:
                raise DeserializationError(f"duplicate $id {node_id!r}",
                                           _join(path, "$id"))
            self._ids[node_id] = "in_progress"

        for key, value in state.items():
            self._validate(value, _join(_join(path, "$state"), key), depth + 1)
        if args:
            for index, item in enumerate(args):
                self._validate(item, _join(_join(path, "$args"), index),
                               depth + 1)

        if node_id is not None:
            self._ids[node_id] = "done"

    # ------------------------------------------------------------------
    # phase 2: construction (input is fully validated at this point)
    # ------------------------------------------------------------------
    def _build(self, node: Any) -> Any:
        if isinstance(node, list):
            return [self._build(item) for item in node]
        if isinstance(node, dict):
            if "$ref" in node:
                return self._memo[node["$ref"]]
            if "$type" in node:
                return self._build_typed(node)
            return {key: self._build(value) for key, value in node.items()}
        return node

    def _build_typed(self, node: dict) -> Any:
        rule = self.whitelist.types.get(node["$type"]) or \
            self.whitelist.types.get(self.whitelist.aliases.get(node["$type"]))
        cls = import_symbol(rule.name)
        state = {key: self._build(value)
                 for key, value in node.get("$state", {}).items()}
        args = [self._build(value) for value in node.get("$args", [])]
        if rule.constructor:
            factory = getattr(cls, rule.constructor, None)
            if factory is None:
                raise DeserializationError(
                    f"whitelisted constructor {rule.constructor!r} not found "
                    f"on {rule.name}")
            obj = factory(**state)
        else:
            obj = cls(*args, **state)
        node_id = node.get("$id")
        if node_id is not None:
            self._memo[node_id] = obj
        return obj
