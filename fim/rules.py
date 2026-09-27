"""显式、可审计的排除规则。

每条规则都有稳定 id、人类可读原因、作用路径。规则匹配会被
IntegrityMonitor 写入审计日志（字段 rule_hits / reason），
因此"排除了什么"始终可查，排除规则无法悄悄吞掉告警。

模式语法（相对监控根 root 的路径，使用 / 分隔）：
  *        匹配单层路径内任意字符（不含 /）
  **       匹配任意层级（含 0 层）目录
  ?        匹配单个字符（不含 /）
其它字符按字面量匹配。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional


def _compile(pattern: str) -> "re.Pattern[str]":
    tokens: List[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                # ** 后面可选的 / 一并吞掉，实现目录通配
                j = i + 2
                if j < len(pattern) and pattern[j] == "/":
                    j += 1
                    tokens.append("(?:.*/)?")
                else:
                    tokens.append(".*")
                i = j
                continue
            tokens.append("[^/]*")
        elif ch == "?":
            tokens.append("[^/]")
        elif ch == "[":
            j = i + 1
            if j < len(pattern) and pattern[j] in "!^":
                j += 1
            if j < len(pattern) and pattern[j] == "]":
                j += 1
            while j < len(pattern) and pattern[j] != "]":
                j += 1
            if j >= len(pattern):
                tokens.append(re.escape("["))
            else:
                inner = pattern[i + 1 : j]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                tokens.append("[" + inner + "]")
                i = j
        else:
            tokens.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(tokens) + "$")


@dataclass(frozen=True)
class Rule:
    rule_id: str
    pattern: str
    reason: str

    def matches(self, relpath: str) -> bool:
        normalized = relpath.replace("\\", "/").lstrip("./")
        return _compile(self.pattern).match(normalized) is not None


class RuleSet:
    def __init__(self, rules: Iterable[Rule] = ()):
        self.rules: List[Rule] = list(rules)

    def match(self, relpath: str) -> Optional[Rule]:
        """返回第一条命中的规则（规则顺序即优先级），未命中返回 None。"""
        for rule in self.rules:
            if rule.matches(relpath):
                return rule
        return None

    def add(self, rule: Rule) -> None:
        if any(r.rule_id == rule.rule_id for r in self.rules):
            raise ValueError(f"duplicate rule id: {rule.rule_id}")
        self.rules.append(rule)

    def describe(self) -> str:
        lines = [f"{r.rule_id:24s} {r.pattern:28s} {r.reason}" for r in self.rules]
        return "\n".join(lines)


# 默认规则：临时路径、生成产物、日志/轮转、崩溃转储、编辑器临时文件。
DEFAULT_RULES: List[Rule] = [
    Rule("R001-TMPDIR", "**/tmp/**", "临时目录内容"),
    Rule("R002-CACHE", "**/cache/**", "可重建的缓存/生成产物"),
    Rule("R003-BUILD", "**/build/**", "构建产物目录"),
    Rule("R004-LOG-ROTATED", "**/log/*.log.[0-9]*", "日志轮转归档（含 .gz/.1 等）"),
    Rule("R004-LOG-ROTATED", "**/logs/*.log.[0-9]*", "日志轮转归档（含 .gz/.1 等）"),
    Rule("R005-LOG-LIVE", "**/log/*.log", "存活日志文件（持续追加）"),
    Rule("R005-LOG-LIVE", "**/logs/*.log", "存活日志文件（持续追加）"),
    Rule("R006-EDITOR-TMP", "**/.*.swp", "编辑器交换文件"),
    Rule("R007-EDITOR-TMP", "**/*~", "编辑器备份文件"),
    Rule("R008-EDITOR-TMP", "**/.#*", "编辑器锁文件"),
    Rule("R009-PY-PYC", "**/__pycache__/**", "Python 字节码缓存"),
    Rule("R010-CORE-DUMP", "**/core.[0-9]*", "崩溃转储"),
]
