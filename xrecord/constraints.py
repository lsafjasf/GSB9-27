"""跨记录不变量的声明形式。

每个约束描述：
  * name        约束名（锁与错误信息的标识）；
  * tables      该约束依赖哪些表（用于判定某次提交是否需要校验它）；
  * check(view) 在“提交后的假想视图”上运行，返回 None 表示通过，
                否则返回非空字符串说明违约原因，也可以直接
                raise ConstraintViolation。

内置约束：
  SumCap   —— 单表（可分组）某数值列总量上限；
  SumEqual —— 两张表某数值列总量必须一致。
也可以用 GenericConstraint 直接挂任意函数，实现任意跨记录不变量
（例如两表逐行对账、预算=已分配+剩余 等）。
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional, Sequence


class ConstraintViolation(Exception):
    """校验失败。携带约束名与原因，便于调用方定位。"""

    def __init__(self, constraint: str, reason: str):
        self.constraint = constraint
        self.reason = reason
        super().__init__("约束 %r 被违反: %s" % (constraint, reason))


class Constraint:
    """约束基类。子类实现 check。"""

    def __init__(self, name: str, tables: Iterable[str]):
        self.name = name
        self.tables = frozenset(tables)

    def check(self, view) -> Optional[str]:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return "<%s %s tables=%s>" % (
            type(self).__name__, self.name, sorted(self.tables))


class GenericConstraint(Constraint):
    """用任意函数构造约束。

    fn(view) -> None | str，抛 ConstraintViolation 同样算失败。
    """

    def __init__(self, name: str, tables: Iterable[str],
                 fn: Callable[[object], Optional[str]]):
        super().__init__(name, tables)
        self._fn = fn

    def check(self, view) -> Optional[str]:
        return self._fn(view)


def _group_of(row: dict, group_cols: Sequence[str]):
    if not group_cols:
        return None
    return tuple(row.get(col) for col in group_cols)


def _number(value) -> float:
    if value is None:
        return 0
    return value


class SumCap(Constraint):
    """table.column 的数值总和（可按 group_cols 分组）不得超过 cap。

    谓词 where(row)->bool 可用于只统计部分记录，例如只统计未删除单据。
    """

    def __init__(self, name: str, table: str, column: str,
                 cap, group_cols: Iterable[str] = (),
                 where: Optional[Callable[[dict], bool]] = None):
        super().__init__(name, [table])
        self.table = table
        self.column = column
        self.cap = cap
        self.group_cols = tuple(group_cols)
        self.where = where

    def check(self, view) -> Optional[str]:
        totals = view.sum_by(
            self.table, self.column, self.group_cols, self.where)
        for group, total in totals.items():
            if total > self.cap:
                if self.group_cols:
                    where_it = "分组 %s=%r" % (self.group_cols, group)
                else:
                    where_it = "全表"
                return ("%s %s 总量 %s 超过上限 %s"
                        % (where_it, self.column, total, self.cap))
        return None


class SumEqual(Constraint):
    """两张表各自 column 的总量必须相等（典型的两表一致性约束）。

    可选分组列，要求两张表按同一组键聚合后逐组一致。
    """

    def __init__(self, name: str, table_a: str, table_b: str,
                 column: str, group_cols: Iterable[str] = ()):
        super().__init__(name, [table_a, table_b])
        self.table_a = table_a
        self.table_b = table_b
        self.column = column
        self.group_cols = tuple(group_cols)

    def check(self, view) -> Optional[str]:
        sums_a = view.sum_by(self.table_a, self.column, self.group_cols)
        sums_b = view.sum_by(self.table_b, self.column, self.group_cols)
        for group in sorted(set(sums_a) | set(sums_b), key=str):
            a = sums_a.get(group, 0)
            b = sums_b.get(group, 0)
            if a != b:
                if self.group_cols:
                    where_it = "分组 %s=%r " % (self.group_cols, group)
                else:
                    where_it = ""
                return ("两表 %s/%s %s列 %s总量不一致: %s != %s"
                        % (self.table_a, self.table_b, self.column,
                           where_it, a, b))
        return None
