"""Determinism, parse-validity and structure tests for codegen/skeleton."""

import ast
import os
import subprocess
import sys
import unittest

from codegen import CodeGen, safe_ident
from skeleton import generate

HERE = os.path.dirname(os.path.abspath(__file__))

SAMPLE_CONFIG = {
    "module_doc": "Sample generated module.",
    "imports": {"os", "sys", "re"},          # set: exercises sort_key
    "constants": {"LIMIT": 10, "PREFIX": "x"},
    "classes": [
        {"name": "Handler", "doc": "Base handler.",
         "methods": [
             {"name": "class", "args": ["self", "value"],   # keyword clash
              "doc": "Store value.", "body": ["self.value = value"]},
             {"name": "reset", "args": ["self"],
              "body": ["self.value = None"]},
             {"name": "noop", "args": ["self"], "enabled": False,
              "body": ["pass"]},
         ]},
        {"name": "Ghost", "enabled": False, "methods": []},  # dropped
        {"name": "Empty", "methods": []},                    # dropped (empty)
    ],
    "functions": [
        {"name": "main", "args": [], "body": ["return 0"]},
        {"name": "1st-helper", "args": ["x"], "body": ["return x"]},
    ],
}


class StructureTests(unittest.TestCase):
    def test_exact_output(self):
        gen = CodeGen()
        gen.line("import os")
        gen.blank()
        with gen.block("def f(a):"):
            with gen.block("if a:"):
                gen.line("return 1")
            gen.line("return 0")
        self.assertEqual(
            gen.render(),
            "import os\n"
            "\n"
            "def f(a):\n"
            "    if a:\n"
            "        return 1\n"
            "    return 0\n",
        )

    def test_indent_unit_is_structural(self):
        gen = CodeGen(indent="\t")
        with gen.block("while True:"):
            with gen.block("if x:"):
                gen.line("break")
        self.assertEqual(gen.render(), "while True:\n\tif x:\n\t\tbreak\n")

    def test_empty_blocks_omitted(self):
        gen = CodeGen()
        gen.line("top = 1")
        with gen.block("def gone():"):
            pass
        with gen.block("def also_gone():"):
            gen.blank()
            with gen.block("if x:"):  # nested empties collapse upward
                pass
        gen.line("bottom = 2")
        self.assertEqual(gen.render(), "top = 1\nbottom = 2\n")

    def test_empty_block_pass_policy(self):
        gen = CodeGen(on_empty="pass")
        with gen.block("def kept():"):
            pass
        with gen.block("def dropped():", on_empty="omit"):
            pass
        self.assertEqual(gen.render(), "def kept():\n    pass\n")

    def test_conditional(self):
        gen = CodeGen()
        with gen.when(True, "def yes():"):
            gen.line("return 1")
        with gen.when(False, "def no():"):
            gen.line("return 2")
        with gen.when(False):
            gen.line("gone = 1")
        with gen.when(True):
            gen.line("kept = 1")
        self.assertEqual(gen.render(),
                         "def yes():\n    return 1\nkept = 1\n")

    def test_for_each_sorted(self):
        gen = CodeGen()
        gen.for_each({"b": 2, "a": 1}.items(),
                     lambda g, kv: g.line("%s = %d" % kv),
                     sort_key=lambda kv: kv[0])
        self.assertEqual(gen.render(), "a = 1\nb = 2\n")

    def test_comments_and_blanks(self):
        gen = CodeGen()
        gen.comment("header\n\nsecond paragraph")
        with gen.block("def f():"):
            gen.comment("inner note")
            gen.blank()
            gen.line("return 1")
        out = gen.render()
        self.assertEqual(
            out,
            "# header\n#\n# second paragraph\n"
            "def f():\n    # inner note\n\n    return 1\n",
        )
        for line in out.splitlines():
            self.assertEqual(line, line.rstrip(), "trailing whitespace")

    def test_deep_nesting(self):
        # CPython parser caps indentation at 100 levels; stay below it.
        depth = 90
        gen = CodeGen()
        stack = [gen.block("if level_%d:" % i) for i in range(depth)]
        for ctx in stack:
            ctx.__enter__()
        gen.line("deep = True")
        for ctx in reversed(stack):
            ctx.__exit__(None, None, None)
        lines = gen.render_lines()
        self.assertEqual(lines[-1], "    " * depth + "deep = True")
        ast.parse(gen.render())  # parser accepts deep nesting

    def test_safe_ident(self):
        self.assertEqual(safe_ident("class"), "class_")
        self.assertEqual(safe_ident("def"), "def_")
        self.assertEqual(safe_ident("match"), "match_")
        self.assertEqual(safe_ident("1st-helper"), "_1st_helper")
        self.assertEqual(safe_ident(""), "_")
        self.assertEqual(safe_ident("ok_name"), "ok_name")
        for raw in ["class", "1st-helper", "", "my key", "for"]:
            self.assertTrue(safe_ident(raw).isidentifier())
            self.assertFalse(__import__("keyword").iskeyword(safe_ident(raw)))

    def test_single_trailing_newline_and_empty_gen(self):
        self.assertEqual(CodeGen().render(), "")
        gen = CodeGen()
        gen.line("x = 1")
        gen.blank()
        gen.blank()
        self.assertTrue(gen.render().endswith("= 1\n"))


class DeterminismTests(unittest.TestCase):
    def test_same_process_repeat(self):
        first = generate(SAMPLE_CONFIG)
        for _ in range(5):
            self.assertEqual(generate(SAMPLE_CONFIG), first)

    def test_across_hash_seeds(self):
        script = (
            "import sys; sys.path.insert(0, %r);"
            "from test_codegen import SAMPLE_CONFIG;"
            "from skeleton import generate;"
            "sys.stdout.write(generate(SAMPLE_CONFIG))" % HERE
        )
        outputs = []
        for seed in ("0", "1", "42"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, env=env, check=True)
            outputs.append(proc.stdout)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])


class ParseTests(unittest.TestCase):
    def test_sample_config_parses(self):
        source = generate(SAMPLE_CONFIG)
        tree = ast.parse(source)  # raises SyntaxError if invalid
        names = {n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        self.assertIn("Handler", names)
        self.assertIn("main", names)
        self.assertIn("_1st_helper", names)     # keyword/ident mangled
        self.assertNotIn("Ghost", names)        # disabled class dropped
        self.assertNotIn("Empty", names)        # empty class dropped

    def test_keyword_only_config_parses(self):
        config = {"functions": [
            {"name": kw, "args": ["x"], "body": ["return x"]}
            for kw in ["class", "def", "return", "import", "lambda"]]}
        ast.parse(generate(config))

    def test_deep_nesting_parses(self):
        depth = 60
        gen = CodeGen()
        ctxs = []
        for i in range(depth):
            ctx = gen.block("if level_%d > 0:" % i)
            ctx.__enter__()
            ctxs.append(ctx)
        gen.line("result = %d" % depth)
        for ctx in reversed(ctxs):
            ctx.__exit__(None, None, None)
        ast.parse(gen.render())


if __name__ == "__main__":
    unittest.main()
