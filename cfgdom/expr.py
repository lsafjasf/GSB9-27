"""表达式解析、求值与重命名（SSA 构造与解释器共用）。

表达式取 Python 表达式的一个安全子集：
    整数/布尔常量、变量、+ - * // %、一元 + - not、
    比较（< <= > >= == !=，支持链式）、and / or。
通过 ast 解析并白名单求值，禁止其它一切节点。
"""

from __future__ import annotations

import ast
from typing import Callable, Dict, List


def parse_expr(text: str) -> ast.Expression:
    return ast.parse(text.strip(), mode="eval")


def expr_vars(text: str) -> List[str]:
    """按出现顺序列出表达式中的变量名（去重）。"""
    names: List[str] = []
    for node in ast.walk(parse_expr(text)):
        if isinstance(node, ast.Name) and node.id not in names:
            names.append(node.id)
    return names


class _Renamer(ast.NodeTransformer):
    def __init__(self, fn: Callable[[str], str]):
        self.fn = fn

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self.fn(node.id)
        return node


def rename_expr(text: str, fn: Callable[[str], str]) -> str:
    """把表达式中的每个变量名 x 替换为 fn(x)，返回新的表达式文本。"""
    tree = _Renamer(fn).visit(parse_expr(text))
    return ast.unparse(tree)


_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
}
_CMP_OPS = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


def _eval(node: ast.AST, env: Dict[str, int]):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, bool)):
            return node.value
        raise ValueError(f"不支持的常量: {node.value!r}")
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp):
        val = _eval(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return +val
        if isinstance(node.op, ast.Not):
            return not val
        raise ValueError(f"不支持的一元运算: {ast.dump(node.op)}")
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, env)
            if type(op) not in _CMP_OPS:
                raise ValueError(f"不支持的比较运算: {ast.dump(op)}")
            if not _CMP_OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for v in node.values:
                result = _eval(v, env)
                if not result:
                    return result
            return result
        if isinstance(node.op, ast.Or):
            result = False
            for v in node.values:
                result = _eval(v, env)
                if result:
                    return result
            return result
    raise ValueError(f"不支持的表达式节点: {ast.dump(node)}")


def eval_expr(text: str, env: Dict[str, int]):
    """安全求值表达式；变量值取自 env（可用带默认值的 dict 子类）。"""
    return _eval(parse_expr(text).body, env)
