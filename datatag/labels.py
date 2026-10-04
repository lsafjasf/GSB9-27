"""合规标签定义与"只能收紧"的表驱动合并规则。

标签由两部分组成：
  level        分级（谁能看），等级越高越敏感，合并时取更严格者；
  restrictions 限制标签集合（能怎么用），如加密存储、禁止出境、禁止聚合等，
               合并时取并集（任何一方的限制都必须保留）。

无标签数据 ``UNLABELED`` 是最宽松的分级。无标签绝不等于公开：
``merge(UNLABELED, X) = X``，单独使用无标签数据时由传播/传输层
产生 ``UNLABELED_SOURCE`` 告警，要求人工确认来源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from itertools import product
from typing import Dict, FrozenSet, Iterable, List, Tuple


class Level(IntEnum):
    """数据分级，数值越大越敏感（越收紧）。"""

    UNLABELED = 0
    PUBLIC = 1
    INTERNAL = 2
    CONFIDENTIAL = 3
    RESTRICTED = 4
    SECRET = 5


# 限制标签：附加在分级之上的处理要求，互相独立，合并取并集。
RESTRICTION_ENCRYPT = "ENCRYPT_AT_REST"      # 必须加密存储
RESTRICTION_NO_EXPORT = "NO_CROSS_BORDER"    # 禁止跨境传输
RESTRICTION_NO_AGGREGATE = "NO_AGGREGATE"    # 禁止用于聚合/统计建模
RESTRICTION_PII = "PII"                      # 含个人身份信息
RESTRICTION_AUDIT = "AUDIT_ACCESS"           # 访问需审计留痕

ALL_RESTRICTIONS: FrozenSet[str] = frozenset(
    {
        RESTRICTION_ENCRYPT,
        RESTRICTION_NO_EXPORT,
        RESTRICTION_NO_AGGREGATE,
        RESTRICTION_PII,
        RESTRICTION_AUDIT,
    }
)


class UnknownLabelError(ValueError):
    """遇到系统未登记的标签（防止下游伪造一个"更宽松"的标签绕过合并）。"""


@dataclass(frozen=True)
class Label:
    """不可变合规标签：分级 + 限制集合。"""

    level: Level
    restrictions: FrozenSet[str] = frozenset()

    def __post_init__(self) -> None:
        unknown = set(self.restrictions) - ALL_RESTRICTIONS
        if unknown:
            raise UnknownLabelError(f"未知限制标签: {sorted(unknown)}")

    @property
    def is_unlabeled(self) -> bool:
        return self.level == Level.UNLABELED and not self.restrictions

    def covers(self, other: "Label") -> bool:
        """``self`` 是否至少和 ``other`` 一样严格（self >= other）。"""
        return (
            self.level >= other.level
            and self.restrictions >= other.restrictions
        )

    def to_raw(self) -> dict:
        return {
            "level": self.level.name,
            "restrictions": sorted(self.restrictions),
        }

    def __str__(self) -> str:
        if self.is_unlabeled:
            return "UNLABELED"
        tail = (
            "+{" + ",".join(sorted(self.restrictions)) + "}"
            if self.restrictions
            else ""
        )
        return f"{self.level.name}{tail}"


def label_from_raw(raw: dict) -> Label:
    """从传输/存储的普通 dict 重建标签，未知值直接拒绝。"""
    if not isinstance(raw, dict):
        raise UnknownLabelError("标签必须是对象")
    try:
        level = Level[raw["level"]]
    except (KeyError, TypeError) as exc:
        raise UnknownLabelError("标签缺少 level 字段") from exc
    except ValueError as exc:
        raise UnknownLabelError(f"未知分级: {raw.get('level')!r}") from exc
    restrictions = raw.get("restrictions", [])
    if not isinstance(restrictions, list):
        raise UnknownLabelError("restrictions 必须是列表")
    return Label(level, frozenset(restrictions))


# 常用标签常量。
UNLABELED = Label(Level.UNLABELED)
PUBLIC = Label(Level.PUBLIC)
INTERNAL = Label(Level.INTERNAL)
CONFIDENTIAL = Label(Level.CONFIDENTIAL)
RESTRICTED = Label(Level.RESTRICTED)
SECRET = Label(Level.SECRET)


@dataclass(frozen=True)
class MergeResult:
    """一次合并的结果：合并后标签、冲突明细、收紧方向。"""

    label: Label
    conflicts: Tuple["Conflict", ...] = field(default_factory=tuple)
    tightened: bool = False
    unlabeled_input: bool = False

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


@dataclass(frozen=True)
class Conflict:
    """单条冲突：哪个维度、两边取值、裁决取值。"""

    dimension: str          # "level" 或 "restriction:<名称>"
    left: object
    right: object
    resolved: object


def _merge_two(left: Label, right: Label) -> Tuple[Label, Tuple[Conflict, ...]]:
    conflicts: List[Conflict] = []

    if left.level != right.level:
        resolved_level = max(left.level, right.level)
        conflicts.append(
            Conflict(
                dimension="level",
                left=left.level.name,
                right=right.level.name,
                resolved=resolved_level.name,
            )
        )
    else:
        resolved_level = left.level

    only_right = right.restrictions - left.restrictions
    only_left = left.restrictions - right.restrictions
    if only_left or only_right:
        for name in sorted(only_left | only_right):
            conflicts.append(
                Conflict(
                    dimension=f"restriction:{name}",
                    left="present" if name in left.restrictions else "absent",
                    right="present" if name in right.restrictions else "absent",
                    resolved="present",
                )
            )
    resolved = Label(
        resolved_level,
        left.restrictions | right.restrictions,
    )
    return resolved, tuple(conflicts)


def merge_labels(labels: Iterable[Label]) -> MergeResult:
    """合并任意数量标签，保证结果对每一个输入都 ``covers``（只收紧）。

    空输入是非法的：无中生有产生"无标签"数据会掩盖来源问题，
    无标签来源应显式传入 ``UNLABELED``。
    """
    items = list(labels)
    if not items:
        raise ValueError("至少需要一个标签；无标签数据请显式传 UNLABELED")

    result = items[0]
    all_conflicts: List[Conflict] = []
    unlabeled_input = result.is_unlabeled
    for nxt in items[1:]:
        unlabeled_input = unlabeled_input or nxt.is_unlabeled
        before = result
        result, conflicts = _merge_two(result, nxt)
        all_conflicts.extend(conflicts)
        if not result.covers(before):  # 防御性自检：合并规则永不变宽
            raise AssertionError("合并结果比输入宽松，规则实现有误")

    tightened = any(not result.covers(x) or result != x for x in items)
    return MergeResult(
        label=result,
        conflicts=tuple(all_conflicts),
        tightened=tightened,
        unlabeled_input=unlabeled_input,
    )


# ---------------------------------------------------------------------------
# 合并规则表
# ---------------------------------------------------------------------------

_LEVEL_ORDER: Tuple[Level, ...] = tuple(Level)


def merge_rule_table() -> List[Dict[str, str]]:
    """返回 6x6 分级合并规则表（限制标签始终取并集，规则见模块文档）。

    每一行：left、right、result、是否冲突、说明。
    表是完整的、对称的：任意两种分级组合都能查到唯一裁决。
    """
    rows: List[Dict[str, str]] = []
    for left, right in product(_LEVEL_ORDER, repeat=2):
        resolved = max(left, right)
        conflict = left != right
        if left == right:
            note = "同标签合并，保持不变"
        elif min(left, right) == Level.UNLABELED:
            note = "含无标签数据：按有标签一方处理，并告警人工确认来源"
        else:
            note = "分级冲突：只能收紧，取更严格者"
        rows.append(
            {
                "left": left.name,
                "right": right.name,
                "result": resolved.name,
                "conflict": "是" if conflict else "否",
                "note": note,
            }
        )
    return rows


def restriction_rule_table() -> List[Dict[str, str]]:
    """限制标签的二元合并表（present=带限制，absent=不带）。"""
    rows = []
    for left, right in product(("present", "absent"), repeat=2):
        resolved = "present" if left == "present" or right == "present" else "absent"
        rows.append(
            {
                "left": left,
                "right": right,
                "result": resolved,
                "note": "取并集：任何一方要求即必须保留",
            }
        )
    return rows
