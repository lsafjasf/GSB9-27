"""支持跨记录不变量的微型表存储（线程模型，仅标准库）。

串行化方案
==========
每次提交涉及两种守卫锁，全部按全局固定顺序获取，杜绝死锁：

1. 约束锁 ("c", 约束名)：凡是会改动某约束所依赖表的提交，都必须先拿到
   该约束的锁。于是「读取现状 -> 在假想提交视图上校验 -> 真正写入」这一
   整段对同一约束是原子的：两个各自合法、合起来违约的并发提交不可能同时
   通过校验。
2. 表锁 ("t", 表名)：防止两个不共享约束、但改了同一张无约束表的提交互相
   覆盖（丢失更新）。

回滚方案
========
* 校验阶段完全在事务私有的暂存视图（_View）上进行，真实状态一个字节都
  不会提前被改，所以「校验失败」天然不可能产生部分更新；
* 正式写入采用 copy-on-write：每次 put/delete 都用「整表 dict 替换」的
  方式换入新表对象，行记录一旦写入即不可变。提交开始时保存涉及表的旧
  dict 引用；一旦写入过程中出错（用 fault= 参数注入），立即把旧引用整
  批换回去，连同表版本号一起恢复。
"""
from __future__ import annotations

import threading
from typing import Dict, Iterable, List, Optional, Tuple

from .constraints import Constraint, ConstraintViolation

Mutation = Tuple[str, str, object]  # ("put"|"delete", table, id[, row])


class CommitFault(Exception):
    """由 fault= 注入的模拟提交期故障（仅测试/演示用）。"""


def _as_number(value):
    return 0 if value is None else value


class _View:
    """真实状态 + 本次暂存修改 叠加出来的只读视图，供约束校验使用。"""

    def __init__(self, tables: Dict[str, Dict[object, dict]],
                 staged: Dict[Tuple[str, object], tuple]):
        self._tables = tables
        self._staged = staged

    def _merged(self, table: str) -> Dict[object, dict]:
        merged = dict(self._tables.get(table, {}))
        for (tbl, rid), mut in self._staged.items():
            if tbl != table:
                continue
            if mut[0] == "put":
                merged[rid] = mut[3]
            else:  # delete
                merged.pop(rid, None)
        return merged

    @staticmethod
    def _with_id(rid, row):
        out = dict(row)
        out.setdefault("id", rid)
        return out

    def rows(self, table: str, where=None) -> List[dict]:
        out = []
        for rid, row in self._merged(table).items():
            record = self._with_id(rid, row)
            if where is None or where(record):
                out.append(record)
        return out

    def get(self, table: str, rid) -> Optional[dict]:
        staged = self._staged.get((table, rid))
        if staged is not None:
            if staged[0] == "delete":
                return None
            return self._with_id(rid, staged[3])
        row = self._tables.get(table, {}).get(rid)
        return None if row is None else self._with_id(rid, row)

    def count(self, table: str, where=None) -> int:
        return len(self.rows(table, where))

    def sum_by(self, table: str, column: str,
               group_cols: Iterable[str] = (), where=None) -> Dict[tuple, float]:
        groups = tuple(group_cols)
        totals: Dict[tuple, float] = {}
        for rid, row in self._merged(table).items():
            record = self._with_id(rid, row)
            if where is not None and not where(record):
                continue
            key = tuple(record.get(col) for col in groups) if groups else None
            totals[key] = totals.get(key, 0) + _as_number(record.get(column))
        return totals

    def sum(self, table: str, column: str, where=None) -> float:
        return self.sum_by(table, column, (), where).get(None, 0)


class Store:
    def __init__(self):
        self._tables: Dict[str, Dict[object, dict]] = {}
        self._version: Dict[str, int] = {}
        self._constraints: Dict[str, Constraint] = {}
        # 守卫锁统一存放，键 ("t", 表名) / ("c", 约束名)
        self._guards: Dict[tuple, threading.RLock] = {
            ("t", name): threading.RLock() for name in ()}
        self._register_lock = threading.RLock()

    # ---------- 结构与约束声明 ----------

    def create_table(self, name: str) -> None:
        with self._register_lock:
            if name in self._tables:
                raise ValueError("表已存在: %r" % name)
            self._tables[name] = {}
            self._version[name] = 0
            self._guards[("t", name)] = threading.RLock()

    def add_constraint(self, constraint: Constraint) -> None:
        """声明约束；若现有数据已经违约，立即拒绝注册。"""
        with self._register_lock:
            if constraint.name in self._constraints:
                raise ValueError("约束已存在: %r" % constraint.name)
            for table in constraint.tables:
                if table not in self._tables:
                    raise ValueError("约束依赖的表不存在: %r" % table)
            self._guards[("c", constraint.name)] = threading.RLock()

        keys = sorted([("c", constraint.name)]
                      + [("t", t) for t in constraint.tables])
        acquired = []
        try:
            for key in keys:
                guard = self._guards[key]
                guard.acquire()
                acquired.append(guard)
            # 注册在与提交相同的表锁保护下进行，保证注册瞬间数据满足约束。
            view = _View(self._tables, {})
            reason = constraint.check(view)
            if reason is not None:
                raise ConstraintViolation(constraint.name, reason)
            with self._register_lock:
                self._constraints[constraint.name] = constraint
        finally:
            for guard in reversed(acquired):
                guard.release()

    @property
    def constraint_names(self) -> List[str]:
        return list(self._constraints)

    # ---------- 读取 ----------

    def committed_view(self) -> _View:
        """已提交数据的只读视图（不加锁；单表读始终一致）。"""
        return _View(self._tables, {})

    def locked_view(self) -> _View:
        """拿到全部表/约束锁后的强一致只读快照视图。"""
        with self._register_lock:
            keys = sorted(self._guards)
            guards = [self._guards[k] for k in keys]
        for guard in guards:
            guard.acquire()
        try:
            return _View(self._tables, {})
        finally:
            for guard in reversed(guards):
                guard.release()

    def snapshot(self) -> Dict[str, Dict[object, dict]]:
        """深拷贝快照，用于回滚断言等外部比对。"""
        return {t: {rid: dict(row) for rid, row in tbl.items()}
                for t, tbl in self._tables.items()}

    def versions(self) -> Dict[str, int]:
        return dict(self._version)

    # ---------- 事务 / 提交 ----------

    def transaction(self) -> "Transaction":
        return Transaction(self)

    def _normalize(self, mutations: Iterable[Mutation]):
        staged: Dict[Tuple[str, object], tuple] = {}
        touched = set()
        for mut in mutations:
            op, table, rid = mut[0], mut[1], mut[2]
            if table not in self._tables:
                raise KeyError("表不存在: %r" % table)
            touched.add(table)
            if op == "put":
                payload = dict(mut[3])
                payload["id"] = rid
                staged[(table, rid)] = ("put", table, rid, payload)
            elif op == "delete":
                staged[(table, rid)] = ("delete", table, rid)
            else:
                raise ValueError("未知变更类型: %r" % op)
        return staged, touched

    def commit(self, mutations: Iterable[Mutation], *, fault: Optional[str] = None):
        """提交一批变更。

        任一相关约束不通过 -> 抛 ConstraintViolation，状态不变。
        fault 可取值：
          "before_check"：拿到锁后、校验前注入故障；
          "during_apply"：写入第一条变更后注入故障，触发回滚。
        """
        staged, touched = self._normalize(mutations)
        if not staged:
            return {"applied": 0}

        for _ in range(1024):  # 仅在并发注册新约束时才会重试
            with self._register_lock:
                relevant = [c for c in self._constraints.values()
                            if c.tables & touched]
                keys = sorted({("c", c.name) for c in relevant}
                              | {("t", t) for t in touched})
                guards = [self._guards[key] for key in keys]

            acquired = []
            violation = None
            try:
                for guard in guards:
                    guard.acquire()
                    acquired.append(guard)

                # 拿锁期间可能有新约束注册进来；若相关约束集合变化则重来。
                with self._register_lock:
                    relevant_now = [c for c in self._constraints.values()
                                    if c.tables & touched]
                if {c.name for c in relevant_now} != {c.name for c in relevant}:
                    continue

                if fault == "before_check":
                    raise CommitFault("注入故障：校验开始前")

                view = _View(self._tables, staged)
                for constraint in relevant_now:
                    reason = constraint.check(view)
                    if reason is not None:
                        violation = ConstraintViolation(constraint.name, reason)
                        raise violation

                applied = self._apply(staged, touched, fault)
                return {"applied": applied}
            finally:
                for guard in reversed(acquired):
                    guard.release()
        raise RuntimeError("提交重试次数超限")  # pragma: no cover

    def _apply(self, staged, touched, fault) -> int:
        # 保存旧表对象引用：copy-on-write 下恢复引用即可完整回滚。
        backup = {t: (self._version[t], self._tables[t]) for t in touched}
        applied = 0
        try:
            for mut in staged.values():
                op, table, rid = mut[0], mut[1], mut[2]
                current = self._tables[table]
                if op == "put":
                    new_table = dict(current)
                    new_table[rid] = mut[3]
                    self._tables[table] = new_table
                    self._version[table] += 1
                    applied += 1
                else:
                    if rid in current:
                        new_table = dict(current)
                        del new_table[rid]
                        self._tables[table] = new_table
                        self._version[table] += 1
                    applied += 1
                if fault == "during_apply":
                    raise CommitFault("注入故障：写入过程中")
        except CommitFault:
            for table, (old_version, old_table) in backup.items():
                self._tables[table] = old_table
                self._version[table] = old_version
            raise
        return applied


class Transaction:
    """显式事务：累积 put/delete，最后 commit；with 块退出时自动提交。"""

    def __init__(self, store: Store):
        self._store = store
        self._mutations: List[Mutation] = []
        self._finished = False

    def __enter__(self) -> "Transaction":
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None and not self._finished:
            self.commit()
        return False

    def put(self, table: str, rid, row: dict) -> "Transaction":
        self._mutations.append(("put", table, rid, row))
        return self

    def delete(self, table: str, rid) -> "Transaction":
        self._mutations.append(("delete", table, rid))
        return self

    def commit(self, *, fault: Optional[str] = None):
        if self._finished:
            raise RuntimeError("事务已结束")
        self._finished = True
        return self._store.commit(self._mutations, fault=fault)

    def rollback(self) -> None:
        self._finished = True
        self._mutations.clear()
