"""Declarative, auditable exclusion rules.

Rules are loaded from a JSON document (fim/rules.json by default). Each rule
has a stable id, a category and a regex. Matching is performed against the
POSIX-style path relative to the watch root; the basename alone is also
tested so a rule applies regardless of the install prefix.

Every match is returned by :meth:`RuleSet.match` so callers can persist an
audit record - rules never silently swallow events.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import List, Optional


def _bundled_rules_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json")


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    description: str
    pattern: "re.Pattern[str]"

    def matches(self, relpath: str) -> bool:
        if self.pattern.search(relpath):
            return True
        base = relpath.rsplit("/", 1)[-1]
        return bool(self.pattern.search(base))


class RuleSet:
    def __init__(self, rules: List[Rule], version: int = 1):
        self.version = version
        self.rules = rules

    @classmethod
    def load(cls, path: Optional[str] = None) -> "RuleSet":
        if path is None:
            path = _bundled_rules_path()
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        rules: List[Rule] = []
        for entry in doc["rules"]:
            rules.append(
                Rule(
                    id=entry["id"],
                    category=entry["category"],
                    description=entry.get("description", ""),
                    pattern=re.compile(entry["regex"]),
                )
            )
        return cls(rules, version=int(doc.get("version", 1)))

    def match(self, relpath: str) -> Optional[Rule]:
        for rule in self.rules:
            if rule.matches(relpath):
                return rule
        return None
