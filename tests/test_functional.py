"""功能与回滚自测：单条/批量、注册冲突、两表一致性、整体回滚。"""
import unittest

from xrecord import (
    ConstraintViolation,
    GenericConstraint,
    Store,
    SumCap,
    SumEqual,
)
from xrecord.store import CommitFault


def budget_store(cap=100):
    s = Store()
    s.create_table("orders")
    s.add_constraint(SumCap("total_cap", "orders", "qty", cap))
    return s


class TestSingleAndBatch(unittest.TestCase):
    def test_single_update_accept_and_reject(self):
        s = budget_store()
        s.transaction().put("orders", "a", {"qty": 60}).commit()
        # 单独提交 30 合法
        s.transaction().put("orders", "b", {"qty": 30}).commit()
        self.assertEqual(s.committed_view().sum("orders", "qty"), 90)
        # 再提交 20 单条看似合法，合起来 110 违约
        with self.assertRaises(ConstraintViolation) as cm:
            s.transaction().put("orders", "c", {"qty": 20}).commit()
        self.assertEqual(cm.exception.constraint, "total_cap")
        self.assertEqual(s.committed_view().sum("orders", "qty"), 90)

    def test_batch_update_is_atomic(self):
        s = budget_store(cap=100)
        s.transaction().put("orders", "a", {"qty": 40}).commit()
        tx = s.transaction()
        # 批量里每条都不超，但合起来 130 违约 -> 整批拒绝
        for rid, qty in [("b", 30), ("c", 30), ("d", 30)]:
            tx.put("orders", rid, {"qty": qty})
        with self.assertRaises(ConstraintViolation):
            tx.commit()
        rows = s.committed_view().rows("orders")
        self.assertEqual([r["id"] for r in rows], ["a"])
        self.assertEqual(s.versions()["orders"], 1)

    def test_batch_delete_frees_capacity(self):
        s = budget_store(cap=100)
        tx = s.transaction()
        tx.put("orders", "a", {"qty": 60})
        tx.put("orders", "b", {"qty": 40})
        tx.commit()
        tx2 = s.transaction()
        tx2.delete("orders", "a")
        tx2.put("orders", "c", {"qty": 55})  # 100 - 60 + 55 = 95，合法
        tx2.commit()
        self.assertEqual(s.committed_view().sum("orders", "qty"), 95)
        self.assertIsNone(s.committed_view().get("orders", "a"))

    def test_grouped_cap(self):
        s = Store()
        s.create_table("usage")
        s.add_constraint(
            SumCap("per_team", "usage", "tokens", 10, group_cols=("team",)))
        tx = s.transaction()
        tx.put("usage", "u1", {"tokens": 6, "team": "x"})
        tx.put("usage", "u2", {"tokens": 4, "team": "y"})
        tx.commit()
        with self.assertRaises(ConstraintViolation):
            s.transaction().put("usage", "u3",
                                {"tokens": 1, "team": "x"}).commit()
        # 另一组仍有余量
        s.transaction().put("usage", "u4",
                            {"tokens": 1, "team": "y"}).commit()


class TestCrossTable(unittest.TestCase):
    def _store(self):
        s = Store()
        s.create_table("ledger_debit")
        s.create_table("ledger_credit")
        s.add_constraint(
            SumEqual("balanced", "ledger_debit", "ledger_credit", "amount"))
        return s

    def test_consistent_pair_accepted(self):
        s = self._store()
        tx = s.transaction()
        tx.put("ledger_debit", "d1", {"amount": 100})
        tx.put("ledger_credit", "c1", {"amount": 100})
        tx.commit()
        self.assertEqual(s.committed_view().sum("ledger_debit", "amount"), 100)
        self.assertEqual(s.committed_view().sum("ledger_credit", "amount"), 100)

    def test_unbalanced_pair_rejected_with_no_partial_update(self):
        s = self._store()
        s.transaction().put("ledger_debit", "d1",
                            {"amount": 100}).put("ledger_credit", "c1",
                                                 {"amount": 100}).commit()
        before = s.snapshot()
        tx = s.transaction()
        tx.put("ledger_debit", "d2", {"amount": 50})
        tx.put("ledger_credit", "c2", {"amount": 40})
        with self.assertRaises(ConstraintViolation):
            tx.commit()
        self.assertEqual(s.snapshot(), before)

    def test_two_constraints_must_both_hold(self):
        s = self._store()
        # 再加一个单边上限：借方总量 <= 30
        s.add_constraint(SumCap("debit_cap", "ledger_debit", "amount", 30))
        # 两边相等（30=30，过 balanced），但借方超过 debit_cap
        tx = s.transaction()
        tx.put("ledger_debit", "d1", {"amount": 50})
        tx.put("ledger_credit", "c1", {"amount": 50})
        with self.assertRaises(ConstraintViolation) as cm:
            tx.commit()
        self.assertEqual(cm.exception.constraint, "debit_cap")
        self.assertEqual(s.committed_view().count("ledger_debit"), 0)


class TestConstraintConflict(unittest.TestCase):
    def test_declaring_on_already_violating_state_is_rejected(self):
        # 约束本身冲突：已有数据违约时，约束注册必须失败
        s = Store()
        s.create_table("orders")
        s.transaction().put("orders", "a", {"qty": 999}).commit()
        with self.assertRaises(ConstraintViolation):
            s.add_constraint(SumCap("cap", "orders", "qty", 100))
        self.assertNotIn("cap", s.constraint_names)
        # 注册失败不得影响后续正常写入与约束列表
        s.transaction().put("orders", "b", {"qty": 1}).commit()

    def test_duplicate_constraint_name(self):
        s = budget_store()
        with self.assertRaises(ValueError):
            s.add_constraint(SumCap("total_cap", "orders", "qty", 1))

    def test_generic_constraint(self):
        # 任意跨记录不变量：所有 qty 必须为正且 id 前缀合法
        s = Store()
        s.create_table("orders")

        def no_negative(view):
            bad = [r["id"] for r in view.rows("orders")
                   if r.get("qty", 0) < 0]
            return "存在负数量记录: %s" % bad if bad else None

        s.add_constraint(GenericConstraint("no_neg", ["orders"], no_negative))
        s.transaction().put("orders", "a", {"qty": -1})
        with self.assertRaises(ConstraintViolation):
            s.transaction().put("orders", "a", {"qty": -1}).commit()


class TestRollback(unittest.TestCase):
    def test_validation_failure_leaves_zero_trace(self):
        s = budget_store(cap=100)
        s.transaction().put("orders", "seed", {"qty": 60}).commit()
        before = s.snapshot()
        before_versions = s.versions()
        tx = s.transaction()
        tx.put("orders", "x", {"qty": 50})
        with self.assertRaises(ConstraintViolation):
            tx.commit()
        # 数据逐字节不变，版本号也不变
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(s.versions(), before_versions)

    def test_fault_during_apply_rolls_back(self):
        s = budget_store(cap=1000)
        s.transaction().put("orders", "seed", {"qty": 10}).commit()
        before = s.snapshot()
        before_versions = s.versions()
        tx = s.transaction()
        for i in range(5):
            tx.put("orders", "r%d" % i, {"qty": 1})
        with self.assertRaises(CommitFault):
            tx.commit(fault="during_apply")
        # 部分写入必须全部撤销：快照与版本号都回到提交前
        self.assertEqual(s.snapshot(), before)
        self.assertEqual(s.versions(), before_versions)

    def test_fault_before_check_rolls_back(self):
        s = budget_store(cap=1000)
        before = s.snapshot()
        tx = s.transaction().put("orders", "x", {"qty": 1})
        with self.assertRaises(CommitFault):
            tx.commit(fault="before_check")
        self.assertEqual(s.snapshot(), before)

    def test_context_manager_aborts_on_exception(self):
        s = budget_store()
        before = s.snapshot()
        with self.assertRaises(RuntimeError):
            with s.transaction() as tx:
                tx.put("orders", "a", {"qty": 50})
                tx.put("orders", "b", {"qty": 60})  # 合起来 110 违约
                raise RuntimeError("业务代码中途炸了")
        self.assertEqual(s.snapshot(), before)

    def test_explicit_rollback(self):
        s = budget_store()
        before = s.snapshot()
        tx = s.transaction().put("orders", "a", {"qty": 50})
        tx.rollback()
        self.assertEqual(s.snapshot(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
