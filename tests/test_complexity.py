import ast
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codemetrics.complexity import analyze_file, analyze_source


def complexity_of(source, name=None):
    result = analyze_source(source)
    if name is None:
        assert len(result.functions) == 1
        return result.functions[0].complexity
    for fn in result.functions:
        if fn.qualname == name:
            return fn.complexity
    raise AssertionError(f"function {name} not found")


class TestBaselineAndEdgeCases(unittest.TestCase):
    def test_empty_function(self):
        self.assertEqual(complexity_of("def f():\n    pass\n"), 1)

    def test_expression_only(self):
        self.assertEqual(complexity_of("def f(x):\n    return x * 2 + 1\n"), 1)

    def test_docstring_only(self):
        self.assertEqual(complexity_of('def f():\n    """doc"""\n'), 1)

    def test_long_straight_function_stays_low(self):
        # 1000 straight-line statements: long, but zero decisions.
        body = "\n".join(f"    x{i} = {i}" for i in range(1000))
        source = f"def f():\n{body}\n    return x999\n"
        result = analyze_source(source)
        self.assertEqual(result.functions[0].complexity, 1)
        self.assertGreater(result.functions[0].loc, 1000)

    def test_very_long_branchy_function_scales(self):
        # 超长函数: 500 if-statements -> complexity 501
        body = "\n".join(f"    if x > {i}:\n        y = {i}" for i in range(500))
        source = f"def f(x):\n    y = 0\n{body}\n    return y\n"
        self.assertEqual(complexity_of(source), 501)


class TestBranchSemantics(unittest.TestCase):
    def test_if_elif_else(self):
        src = ("def f(x):\n"
               "    if x == 1:\n        a = 1\n"
               "    elif x == 2:\n        a = 2\n"
               "    else:\n        a = 3\n"
               "    return a\n")
        self.assertEqual(complexity_of(src), 3)  # if + elif; else is free

    def test_nested_ifs_add_up(self):
        src = ("def f(a, b, c):\n"
               "    if a:\n"
               "        if b:\n"
               "            if c:\n"
               "                return 1\n"
               "    return 0\n")
        # 3 ifs + 1 early return
        self.assertEqual(complexity_of(src), 5)

    def test_short_circuit_boolops(self):
        src = "def f(a, b, c, d):\n    if a and b or c and d:\n        return 1\n"
        # if(1) + and(1) + or(1) + and(1)
        self.assertEqual(complexity_of(src), 5)

    def test_boolop_multi_operand(self):
        src = "def f(a, b, c):\n    return a and b and c\n"
        # one BoolOp, 3 operands -> 2 short-circuit points
        self.assertEqual(complexity_of(src), 3)

    def test_early_returns(self):
        src = ("def f(x):\n"
               "    if x is None:\n        return 0\n"
               "    if x < 0:\n        return -1\n"
               "    return 1\n")
        # 2 ifs + 2 early returns (3 returns total, first is free)
        self.assertEqual(complexity_of(src), 5)

    def test_single_return_not_penalized(self):
        src = "def f(x):\n    y = x + 1\n    return y\n"
        self.assertEqual(complexity_of(src), 1)

    def test_exception_handlers(self):
        src = ("def f():\n"
               "    try:\n        g()\n"
               "    except KeyError:\n        a = 1\n"
               "    except (ValueError, TypeError):\n        a = 2\n"
               "    except Exception:\n        a = 3\n"
               "    else:\n        a = 4\n"
               "    finally:\n        a = 5\n")
        # 3 except handlers; try/else/finally are free
        self.assertEqual(complexity_of(src), 4)

    def test_loops(self):
        src = ("def f(xs):\n"
               "    for x in xs:\n        pass\n"
               "    while xs:\n        break\n")
        self.assertEqual(complexity_of(src), 3)  # for + while

    def test_ternary(self):
        src = "def f(x):\n    return 1 if x else 2\n"
        self.assertEqual(complexity_of(src), 2)

    def test_assert(self):
        src = "def f(x):\n    assert x > 0\n    return x\n"
        self.assertEqual(complexity_of(src), 2)

    def test_match_cases(self):
        src = ("def f(v):\n"
               "    match v:\n"
               "        case 1:\n            a = 1\n"
               "        case [x, y]:\n            a = 2\n"
               "        case _:\n            a = 3\n")
        self.assertEqual(complexity_of(src), 4)  # 3 cases

    def test_comprehension(self):
        src = "def f(rows):\n    return [x for r in rows if r for x in r if x > 0]\n"
        # 2 comp_for + 2 comp_if
        self.assertEqual(complexity_of(src), 5)

    def test_nested_function_not_counted_in_outer(self):
        src = ("def outer():\n"
               "    def inner(x):\n"
               "        if x:\n            return 1\n"
               "        return 0\n"
               "    return inner\n")
        result = analyze_source(src)
        by_name = {f.qualname: f for f in result.functions}
        self.assertEqual(by_name["outer"].complexity, 1)
        self.assertEqual(by_name["outer.inner"].complexity, 3)

    def test_lambda_not_counted_in_enclosing(self):
        src = "def f(xs):\n    key = lambda x: x if x else 0\n    return sorted(xs, key=key)\n"
        self.assertEqual(complexity_of(src), 1)

    def test_methods_qualified(self):
        src = ("class A:\n"
               "    def m(self, x):\n"
               "        if x:\n            return 1\n"
               "        return 0\n")
        result = analyze_source(src)
        self.assertIn("A.m", {f.qualname for f in result.functions})


class TestGeneratedCode(unittest.TestCase):
    def test_generated_file_many_functions(self):
        # Simulates machine-generated code: 2000 identical small functions.
        chunks = []
        for i in range(2000):
            chunks.append(
                f"def gen_{i}(x):\n"
                f"    if x == {i}:\n"
                f"        return {i}\n"
                f"    return -1\n"
            )
        source = "\n".join(chunks)
        result = analyze_source(source, path="generated.py")
        self.assertEqual(len(result.functions), 2000)
        # if(1) + early_return(1) = 3 for every generated function
        self.assertTrue(all(f.complexity == 3 for f in result.functions))

    def test_generated_file_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "gen.py")
            with open(path, "w", encoding="utf-8") as fh:
                for i in range(500):
                    fh.write(f"def g{i}(x):\n    return x + {i}\n")
            result = analyze_file(path)
            self.assertEqual(len(result.functions), 500)
            self.assertTrue(all(f.complexity == 1 for f in result.functions))


class TestModuleLevel(unittest.TestCase):
    def test_module_level_decisions(self):
        src = ("import os\n"
               "if os.environ.get('X'):\n    A = 1\n"
               "else:\n    A = 2\n"
               "def f():\n    if True:\n        pass\n")
        result = analyze_source(src)
        self.assertEqual(result.module_level_complexity, 2)  # top-level if
        self.assertEqual(result.functions[0].complexity, 2)  # def-body if


if __name__ == "__main__":
    unittest.main()
