"""codegen 库与示例生成器的自测（仅标准库 unittest）。

运行：python3 -m unittest test_codegen -v
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from codegen import CodeBuilder, safe_identifier
from example_generator import DEMO_CONFIG, generate


def parse_ok(source: str) -> None:
    ast.parse(source)  # 抛 SyntaxError 即失败


class TestIndentation(unittest.TestCase):
    def test_indent_derived_from_structure(self):
        b = CodeBuilder()
        b.line("import os")
        with b.block("def f():"):
            b.line("x = 1")
            with b.block("if x:"):
                b.line("return x")
        out = b.render()
        self.assertEqual(
            out,
            "import os\n"
            "def f():\n"
            "    x = 1\n"
            "    if x:\n"
            "        return x\n",
        )
        parse_ok(out)

    def test_no_manual_spaces_needed(self):
        b = CodeBuilder()
        with b.block("class A:"):
            with b.block("def m(self):"):
                b.line("pass")
        self.assertEqual(b.render(), "class A:\n    def m(self):\n        pass\n")

    def test_deep_nesting(self):
        depth = 60
        b = CodeBuilder()
        ctx = [b.block(f"if level_{i}:") for i in range(depth)]
        for c in ctx:
            c.__enter__()
        b.line("leaf = True")
        for c in reversed(ctx):
            c.__exit__(None, None, None)
        out = b.render()
        self.assertIn("    " * depth + "leaf = True", out)
        parse_ok(out)


class TestEmptyBlockOmission(unittest.TestCase):
    def test_empty_block_fully_omitted(self):
        b = CodeBuilder()
        b.line("before = 1")
        with b.block("def gone():"):
            pass  # 空块
        b.line("after = 2")
        out = b.render()
        self.assertEqual(out, "before = 1\nafter = 2\n")
        self.assertNotIn("gone", out)

    def test_nested_empty_blocks_omitted(self):
        b = CodeBuilder()
        with b.block("class Outer:"):
            with b.block("def inner():"):
                with b.block("if nothing:"):
                    pass
        self.assertEqual(b.render(), "")  # 层层为空，整体消失

    def test_empty_block_with_pass_strategy(self):
        b = CodeBuilder()
        with b.block("class Empty:", on_empty="pass"):
            pass
        self.assertEqual(b.render(), "class Empty:\n    pass\n")
        parse_ok(b.render())

    def test_blank_only_block_counts_as_empty(self):
        b = CodeBuilder()
        b.line("x = 1")
        with b.block("def g():"):
            b.blank()
            b.blank()
        b.line("y = 2")
        self.assertEqual(b.render(), "x = 1\ny = 2\n")


class TestConditionAndLoop(unittest.TestCase):
    def test_when_false_suppresses_header_and_body(self):
        b = CodeBuilder()
        b.line("a = 1")
        with b.block("def off():", when=False):
            b.line("unreachable = True")
        b.line("b = 2")
        self.assertEqual(b.render(), "a = 1\nb = 2\n")

    def test_when_helper(self):
        b = CodeBuilder()
        b.when(True, lambda bb: bb.line("yes = 1"))
        b.when(False, lambda bb: bb.line("no = 1"))
        self.assertEqual(b.render(), "yes = 1\n")

    def test_for_each_over_set_is_sorted(self):
        outs = set()
        for _ in range(5):
            b = CodeBuilder()
            b.for_each({"gamma", "alpha", "beta"},
                       lambda bb, it: bb.line(f"{it} = 1"))
            outs.add(b.render())
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs.pop(), "alpha = 1\nbeta = 1\ngamma = 1\n")


class TestCommentsAndBlankLines(unittest.TestCase):
    def test_comment_and_blank(self):
        b = CodeBuilder()
        b.comment("模块说明")
        b.blank()
        with b.block("def f():"):
            b.comment("行内注释")
            b.line("return 1")
        out = b.render()
        self.assertEqual(
            out,
            "# 模块说明\n"
            "\n"
            "def f():\n"
            "    # 行内注释\n"
            "    return 1\n",
        )
        parse_ok(out)

    def test_blank_run_collapsed_and_edges_stripped(self):
        b = CodeBuilder()
        b.blank()
        b.line("x = 1")
        for _ in range(5):
            b.blank()
        b.line("y = 2")
        b.blank()
        out = b.render()
        self.assertEqual(out, "x = 1\n\n\ny = 2\n")  # 折叠到最多 2 个空行

    def test_multiline_comment(self):
        b = CodeBuilder()
        b.comment("第一行\n第二行")
        self.assertEqual(b.render(), "# 第一行\n# 第二行\n")


class TestKeywordConflicts(unittest.TestCase):
    def test_safe_identifier_keywords(self):
        self.assertEqual(safe_identifier("class"), "class_")
        self.assertEqual(safe_identifier("import"), "import_")
        self.assertEqual(safe_identifier("match"), "match_")  # 软关键字
        self.assertEqual(safe_identifier("normal_name"), "normal_name")

    def test_safe_identifier_invalid_chars(self):
        self.assertEqual(safe_identifier("2fast"), "_2fast")
        self.assertEqual(safe_identifier("a-b c"), "a_b_c")
        self.assertEqual(safe_identifier(""), "_")

    def test_config_with_keyword_names_parses(self):
        config = {
            "constants": {"class": 1, "import": 2},
            "classes": [{
                "name": "def",  # 与关键字冲突的类名
                "attributes": {"for": 0},
                "methods": [{"name": "while", "args": ["self", "in"],
                             "body": ["return in_"]}],
            }],
            "functions": [{"name": "lambda", "args": ["global"],
                           "body": ["return global_"]}],
        }
        out = generate(config)
        self.assertIn("class_ = 1", out)
        self.assertIn("class def_:", out)
        parse_ok(out)


class TestDeterminism(unittest.TestCase):
    def test_same_config_same_bytes_in_process(self):
        self.assertEqual(generate(DEMO_CONFIG), generate(DEMO_CONFIG))

    def test_same_config_same_bytes_across_processes_and_hash_seeds(self):
        here = os.path.dirname(os.path.abspath(__file__))
        outputs = []
        for seed in ("0", "1", "42"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            proc = subprocess.run(
                [sys.executable, "example_generator.py"],
                cwd=here, env=env, capture_output=True, check=True,
            )
            outputs.append(proc.stdout)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])
        parse_ok(outputs[0].decode("utf-8"))

    def test_render_is_pure(self):
        b = CodeBuilder()
        with b.block("def f():"):
            b.line("pass")
        first = b.render()
        self.assertEqual(first, b.render())  # 重复渲染一致


class TestGeneratedDemoConfig(unittest.TestCase):
    def test_demo_config_parses_and_shape(self):
        out = generate(DEMO_CONFIG)
        parse_ok(out)
        self.assertIn("class Handler(object):", out)
        self.assertIn("    # 入口方法", out)
        self.assertNotIn("disabled_feature", out)  # when=False 被省略
        self.assertIn("class Empty:\n    pass\n", out)
        tree = ast.parse(out)
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        self.assertEqual(names, {"Handler", "Empty"})


class TestCLI(unittest.TestCase):
    def test_cli_writes_parseable_file(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with tempfile.TemporaryDirectory() as td:
            cfg = os.path.join(td, "cfg.json")
            out = os.path.join(td, "out.py")
            with open(cfg, "w", encoding="utf-8") as fh:
                fh.write('{"functions": [{"name": "main", "args": [], '
                         '"body": ["return 0"]}]}')
            subprocess.run([sys.executable, "example_generator.py", cfg, "-o", out],
                           cwd=here, check=True, capture_output=True)
            with open(out, encoding="utf-8") as fh:
                parse_ok(fh.read())


if __name__ == "__main__":
    unittest.main()
