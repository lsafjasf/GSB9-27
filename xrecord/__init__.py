"""跨记录不变量校验库（仅标准库）。

公开接口：
    Store, Transaction, Constraint, GenericConstraint,
    SumCap, SumEqual, ConstraintViolation
"""
from .constraints import (
    Constraint,
    GenericConstraint,
    SumCap,
    SumEqual,
    ConstraintViolation,
)
from .store import Store, Transaction

__all__ = [
    "Store",
    "Transaction",
    "Constraint",
    "GenericConstraint",
    "SumCap",
    "SumEqual",
    "ConstraintViolation",
]
