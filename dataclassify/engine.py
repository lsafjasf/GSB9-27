"""分级引擎：收集规则证据、按优先级表裁决冲突、输出可解释结果。"""
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .levels import LEVELS, level_rank
from .rules import DEFAULT_LEVEL, DEFAULT_LEVEL_REASON, DEFAULT_RULES, Rule


def mask(value: str) -> str:
    """证据中的样例脱敏：保留前 3 后 2，其余打码。"""
    text = str(value)
    if len(text) <= 5:
        return text[0] + "***" if text else ""
    return text[:3] + "***" + text[-2:]


@dataclass
class Evidence:
    rule_id: str
    level: str
    priority: int
    matched_by: List[str]
    detail: str
    basis: str


@dataclass
class Conflict:
    winner_rule: str
    loser_rule: str
    winner_level: str
    loser_level: str
    reason: str


@dataclass
class ClassificationResult:
    field_name: str
    final_level: str
    final_level_name: str
    evidences: List[Evidence]
    conflicts: List[Conflict]
    reason: str

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


class Classifier:
    def __init__(self, rules: Sequence[Rule] = DEFAULT_RULES,
                 default_level: str = DEFAULT_LEVEL):
        self.rules = tuple(rules)
        self.default_level = default_level

    def classify(self, field_name: str, samples: Optional[Sequence] = None,
                 source: str = "") -> ClassificationResult:
        samples = list(samples or [])
        evidences: List[Evidence] = []
        for rule in self.rules:
            evidence = self._match(rule, field_name, samples, source)
            if evidence is not None:
                evidences.append(evidence)
        return self._resolve(field_name, evidences)

    def _match(self, rule: Rule, field_name: str, samples, source) -> Optional[Evidence]:
        if not rule.source_hit(source):
            return None
        matched_by: List[str] = []
        details: List[str] = []

        name_pattern = rule.name_hit(field_name)
        if name_pattern:
            matched_by.append("field_name")
            details.append(f"字段名命中 /{name_pattern}/")

        if rule.value_patterns and samples:
            hit_count, hits = rule.value_hits(samples)
            total = len([s for s in samples if str(s or "").strip()])
            ratio = hit_count / total if total else 0.0
            if total and ratio >= rule.value_threshold:
                matched_by.append("value")
                shown = ", ".join(mask(h) for h in hits[:3])
                details.append(
                    f"取值命中 {hit_count}/{total}（阈值 {rule.value_threshold:.0%}），样例 {shown}"
                )

        if rule.sources is not None and rule.source_hit(source):
            matched_by.append("source")
            details.append(f"来源命中「{source}」")

        if not matched_by:
            return None
        return Evidence(
            rule_id=rule.rule_id,
            level=rule.level,
            priority=rule.priority,
            matched_by=matched_by,
            detail="；".join(details),
            basis=rule.basis,
        )

    def _resolve(self, field_name: str, evidences: List[Evidence]) -> ClassificationResult:
        if not evidences:
            return ClassificationResult(
                field_name=field_name,
                final_level=self.default_level,
                final_level_name=LEVELS[self.default_level],
                evidences=[],
                conflicts=[],
                reason=DEFAULT_LEVEL_REASON,
            )

        ordered = sorted(
            evidences,
            key=lambda e: (e.priority, level_rank(e.level)),
            reverse=True,
        )
        winner = ordered[0]
        conflicts: List[Conflict] = []
        for loser in ordered[1:]:
            if loser.level == winner.level:
                continue
            if loser.priority == winner.priority:
                reason = (
                    f"优先级相同（{winner.priority}），按就高原则取 "
                    f"{winner.level}（{LEVELS[winner.level]}）"
                )
            else:
                reason = (
                    f"按优先级表裁决：{winner.rule_id}（优先级 {winner.priority}）"
                    f"高于 {loser.rule_id}（优先级 {loser.priority}）"
                )
            conflicts.append(Conflict(
                winner_rule=winner.rule_id,
                loser_rule=loser.rule_id,
                winner_level=winner.level,
                loser_level=loser.level,
                reason=reason,
            ))

        level_set = {e.level for e in evidences}
        if conflicts:
            reason = (
                f"命中 {len(evidences)} 条规则、{len(level_set)} 个级别，"
                f"经冲突裁决最终定为 {winner.level}（{LEVELS[winner.level]}）"
            )
        else:
            reason = f"命中规则 {winner.rule_id}，定为 {winner.level}（{LEVELS[winner.level]}）"

        return ClassificationResult(
            field_name=field_name,
            final_level=winner.level,
            final_level_name=LEVELS[winner.level],
            evidences=ordered,
            conflicts=conflicts,
            reason=reason,
        )
