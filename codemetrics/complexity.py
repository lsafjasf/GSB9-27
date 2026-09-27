"""Function-level complexity metrics (Python stdlib only).

The metric is an extended McCabe cyclomatic complexity: it estimates the
number of independent execution paths through a function, which correlates
with the number of test cases needed and the cognitive effort to modify the
function safely. See docs/RULES.md for the full scoring rule table.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

DECISION_KINDS = (
    "if",            # if / elif
    "for",           # for / async for
    "while",         # while
    "ternary",       # x if cond else y
    "boolop",        # short-circuit and/or: n operands -> +(n-1)
    "except",        # each except handler
    "assert",        # assert statement
    "match_case",    # each case of a match statement
    "comp_for",      # each `for` clause in a comprehension
    "comp_if",       # each `if` clause in a comprehension
    "early_return",  # every `return` beyond the first
)


@dataclass
class FunctionMetric:
    qualname: str
    lineno: int
    loc: int
    complexity: int
    returns: int
    decisions: dict


@dataclass
class ModuleComplexity:
    path: str
    functions: list
    module_level_complexity: int  # decisions in top-level (non-def) code


class _DecisionCounter(ast.NodeVisitor):
    """Counts decision points. Nested defs/lambdas are NOT descended into:
    they are separate functions and get their own metric."""

    def __init__(self):
        self.decisions = {kind: 0 for kind in DECISION_KINDS}
        self.returns = 0

    def add(self, kind, n=1):
        self.decisions[kind] += n

    # --- nested scopes: stop descent -------------------------------
    def visit_FunctionDef(self, node):
        pass

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        pass

    def visit_ClassDef(self, node):
        pass

    # --- decision points -------------------------------------------
    def visit_If(self, node):
        self.add("if")
        self.generic_visit(node)

    def visit_For(self, node):
        self.add("for")
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_While(self, node):
        self.add("while")
        self.generic_visit(node)

    def visit_IfExp(self, node):
        self.add("ternary")
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        # `a and b and c` is one BoolOp with 3 values -> 2 short-circuit points
        self.add("boolop", len(node.values) - 1)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        self.add("except")
        self.generic_visit(node)

    def visit_Assert(self, node):
        self.add("assert")
        self.generic_visit(node)

    def visit_Return(self, node):
        self.returns += 1
        self.generic_visit(node)

    def visit_comprehension(self, node):
        self.add("comp_for")
        self.add("comp_if", len(node.ifs))
        self.generic_visit(node)

    def visit_Match(self, node):
        self.add("match_case", len(node.cases))
        self.generic_visit(node)


def _finalize(counter):
    decisions = dict(counter.decisions)
    decisions["early_return"] = max(0, counter.returns - 1)
    complexity = 1 + sum(decisions.values())
    return complexity, decisions


def function_complexity(node, qualname=None):
    """Complexity of a single ast.FunctionDef / ast.AsyncFunctionDef."""
    counter = _DecisionCounter()
    for stmt in node.body:
        counter.visit(stmt)
    complexity, decisions = _finalize(counter)
    return FunctionMetric(
        qualname=qualname or node.name,
        lineno=node.lineno,
        loc=(node.end_lineno or node.lineno) - node.lineno + 1,
        complexity=complexity,
        returns=counter.returns,
        decisions=decisions,
    )


def iter_function_nodes(tree):
    """Yield (qualname, node) for every function/method, including nested."""

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + child.name
                yield qual, child
                yield from walk(child, qual + ".")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, prefix + child.name + ".")
            else:
                yield from walk(child, prefix)

    yield from walk(tree, "")


def module_level_complexity(tree):
    """Decision points in top-level code (outside any def/class)."""
    counter = _DecisionCounter()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        counter.visit(stmt)
    complexity, _ = _finalize(counter)
    return complexity


def analyze_source(source, path="<unknown>"):
    """Analyze one source string -> ModuleComplexity."""
    tree = ast.parse(source, filename=path)
    functions = [
        function_complexity(node, qualname=qual)
        for qual, node in iter_function_nodes(tree)
    ]
    return ModuleComplexity(
        path=path,
        functions=functions,
        module_level_complexity=module_level_complexity(tree),
    )


def analyze_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        return analyze_source(fh.read(), path=str(path))
