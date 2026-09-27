"""Structured diff for config/data files with auditable ignore rules.

Standard library only.

Semantics (contract):
- A diff is one of: value_changed, type_changed, key_added, key_removed,
  item_added, item_removed.
- Ignore rules ONLY ever mask `value_changed` diffs ("floating fields").
- Structural diffs (type_changed / key_added / key_removed / item_added /
  item_removed, i.e. array length changes and deleted keys) are ALWAYS
  reported, even when their path falls inside an ignore rule's scope.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

VALUE_CHANGED = "value_changed"
TYPE_CHANGED = "type_changed"
KEY_ADDED = "key_added"
KEY_REMOVED = "key_removed"
ITEM_ADDED = "item_added"
ITEM_REMOVED = "item_removed"

STRUCTURAL_KINDS = frozenset(
    {TYPE_CHANGED, KEY_ADDED, KEY_REMOVED, ITEM_ADDED, ITEM_REMOVED}
)
MASKABLE_KINDS = frozenset({VALUE_CHANGED})


# ---------------------------------------------------------------- paths (JSON-Pointer style)

def escape_segment(seg):
    return str(seg).replace("~", "~0").replace("/", "~1")


def unescape_segment(seg):
    return seg.replace("~1", "/").replace("~0", "~")


def join_path(path, seg):
    return path + "/" + escape_segment(seg)


def split_path(path):
    """'/a/0/b' -> ['a', '0', 'b']; '' -> []"""
    if not path:
        return []
    return [unescape_segment(s) for s in path.split("/")[1:]]


# ---------------------------------------------------------------- diff engine

def _family(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    return "other"


@dataclass
class Diff:
    kind: str
    path: str
    old: object = None
    new: object = None

    @property
    def key(self):
        return (self.kind, self.path)

    def render(self):
        return f"{self.kind} {self.path}: {self.old!r} -> {self.new!r}"


def diff(old, new):
    out = []
    _diff(old, new, "", out)
    return out


def _diff(old, new, path, out):
    fo, fn = _family(old), _family(new)
    if fo != fn:
        out.append(Diff(TYPE_CHANGED, path, old, new))
        return
    if fo == "dict":
        for k in old:
            if k not in new:
                out.append(Diff(KEY_REMOVED, join_path(path, k), old[k], None))
        for k in new:
            if k not in old:
                out.append(Diff(KEY_ADDED, join_path(path, k), None, new[k]))
        for k in old:
            if k in new:
                _diff(old[k], new[k], join_path(path, k), out)
    elif fo == "list":
        common = min(len(old), len(new))
        for i in range(common):
            _diff(old[i], new[i], join_path(path, i), out)
        for i in range(common, len(old)):
            out.append(Diff(ITEM_REMOVED, join_path(path, i), old[i], None))
        for i in range(common, len(new)):
            out.append(Diff(ITEM_ADDED, join_path(path, i), None, new[i]))
    else:
        if old != new:
            out.append(Diff(VALUE_CHANGED, path, old, new))


# ---------------------------------------------------------------- ignore rules

_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$"
)

TYPE_PREDICATES = {
    "iso8601": lambda v: isinstance(v, str) and bool(_ISO8601_RE.match(v)),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def parse_pattern(pattern):
    """'/servers/*/port' -> ['servers','*','port']; 'comment' -> ['**','comment']."""
    pattern = pattern.strip()
    if not pattern:
        raise ValueError("empty path pattern")
    if pattern.startswith("/"):
        return [unescape_segment(s) for s in pattern.split("/")[1:]]
    return ["**"] + [unescape_segment(s) for s in pattern.split("/")]


def match_segments(pattern_segs, path_segs):
    """Glob match: '*' = exactly one segment, '**' = zero or more segments."""
    if not pattern_segs:
        return not path_segs
    head, rest = pattern_segs[0], pattern_segs[1:]
    if head == "**":
        return any(
            match_segments(rest, path_segs[i:]) for i in range(len(path_segs) + 1)
        )
    if not path_segs:
        return False
    if head == "*" or head == path_segs[0]:
        return match_segments(rest, path_segs[1:])
    return False


@dataclass
class Rule:
    """One ignore rule. Exactly one of `path` / `type` must be set.

    Rules only mask `value_changed` diffs; structural diffs are never masked.
    """

    id: str
    path: str | None = None
    type: str | None = None
    reason: str = ""

    def __post_init__(self):
        if (self.path is None) == (self.type is None):
            raise ValueError(
                f"rule {self.id!r}: exactly one of 'path' / 'type' is required"
            )
        if self.type is not None and self.type not in TYPE_PREDICATES:
            raise ValueError(
                f"rule {self.id!r}: unknown type {self.type!r}; "
                f"known: {sorted(TYPE_PREDICATES)}"
            )
        self._segments = parse_pattern(self.path) if self.path is not None else None

    def matches(self, d):
        if d.kind not in MASKABLE_KINDS:
            return False
        if self._segments is not None:
            return match_segments(self._segments, split_path(d.path))
        pred = TYPE_PREDICATES[self.type]
        return pred(d.old) and pred(d.new)

    def describe(self):
        target = f"path={self.path!r}" if self.path is not None else f"type={self.type!r}"
        suffix = f"  # {self.reason}" if self.reason else ""
        return f"{self.id} ({target}){suffix}"


# ---------------------------------------------------------------- audit

@dataclass
class RuleAudit:
    rule: Rule
    masked: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class FilterResult:
    kept: list
    masked: list
    audits: list
    warnings: list  # global warnings


def apply_rules(diffs, rules, warn_abs=100, warn_pct=0.5):
    """Split `diffs` into kept/masked according to `rules`, with audit.

    warn_abs: warn when one rule masks more than this many diffs.
    warn_pct: warn when one rule masks more than this fraction of all raw diffs.
    """
    kept, masked = [], []
    by_rule = {r.id: [] for r in rules}
    for d in diffs:
        hit = None
        if d.kind in MASKABLE_KINDS:
            for r in rules:
                if r.matches(d):
                    hit = r
                    break
        if hit is None:
            kept.append(d)
        else:
            masked.append(d)
            by_rule[hit.id].append(d)

    total = len(diffs)
    audits, warnings = [], []
    for r in rules:
        hits = by_rule[r.id]
        w = []
        if not hits:
            w.append("规则未命中任何差异（可能已失效或路径写错）")
        else:
            if len(hits) > warn_abs:
                w.append(f"屏蔽过多：{len(hits)} 条 > 绝对阈值 {warn_abs}")
            if total and len(hits) / total > warn_pct:
                w.append(
                    f"屏蔽过多：占全部原始差异的 {len(hits)}/{total} "
                    f"({len(hits) / total:.0%}) > 比例阈值 {warn_pct:.0%}"
                )
        audits.append(RuleAudit(rule=r, masked=hits, warnings=w))
        warnings.extend(f"规则 {r.id}: {x}" for x in w)

    if total and len(masked) / total > warn_pct:
        warnings.append(
            f"整体屏蔽比例过高：{len(masked)}/{total} "
            f"({len(masked) / total:.0%}) 的差异被忽略规则吞掉"
        )
    return FilterResult(kept=kept, masked=masked, audits=audits, warnings=warnings)


# ---------------------------------------------------------------- rule file loading

def load_rules_file(path):
    """Load a rules JSON file. Returns (rules, options dict)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rules = [
        Rule(
            id=entry["id"],
            path=entry.get("path"),
            type=entry.get("type"),
            reason=entry.get("reason", ""),
        )
        for entry in data.get("rules", [])
    ]
    options = data.get("audit", {})
    return rules, options


# ---------------------------------------------------------------- reporting

def format_audit(result):
    lines = ["== 忽略规则审计 =="]
    for a in result.audits:
        lines.append(f"规则 {a.rule.describe()}: 屏蔽 {len(a.masked)} 条差异")
        for d in a.masked:
            lines.append(f"  - {d.render()}")
        for w in a.warnings:
            lines.append(f"  !! 告警: {w}")
    if result.warnings:
        lines.append("== 告警汇总 ==")
        lines.extend(f"!! {w}" for w in result.warnings)
    else:
        lines.append("（无告警）")
    return "\n".join(lines)


def diff_to_jsonable(d):
    return {"kind": d.kind, "path": d.path, "old": d.old, "new": d.new}


def result_to_jsonable(result):
    return {
        "kept": [diff_to_jsonable(d) for d in result.kept],
        "masked": [diff_to_jsonable(d) for d in result.masked],
        "audit": [
            {
                "rule": a.rule.id,
                "masked_count": len(a.masked),
                "masked_paths": [d.path for d in a.masked],
                "warnings": a.warnings,
            }
            for a in result.audits
        ],
        "warnings": result.warnings,
    }
