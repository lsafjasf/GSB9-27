"""配置驱动的 Python 代码骨架生成器（示例 + CLI）。

配置（JSON）schema::

    {
      "module_doc": "模块 docstring",
      "imports": ["os", "json"],
      "from_imports": {"typing": ["Any", "Optional"]},
      "constants": {"DEFAULT_TIMEOUT": 30},
      "classes": [
        {"name": "Handler", "bases": ["Base"], "docstring": "...",
         "attributes": {"count": 0},
         "methods": [{"name": "handle", "args": ["self", "request"],
                      "decorators": ["staticmethod"],
                      "body": ["return request"],
                      "comment": "可选行内注释",
                      "when": true}]}
      ],
      "functions": [{"name": "main", "args": [], "body": ["pass"]}]
    }

用法::

    python3 example_generator.py                # 内置演示配置，打印到 stdout
    python3 example_generator.py config.json -o out.py
"""

from __future__ import annotations

import argparse
import ast
import json
import sys

from codegen import CodeBuilder, safe_identifier


def generate(config: dict) -> str:
    """从配置 dict 生成 Python 源码（确定性、经结构缩进）。"""
    b = CodeBuilder()

    if config.get("module_doc"):
        b.line('"""' + str(config["module_doc"]).replace('"""', '\\"\\"\\"') + '"""')
        b.blank()

    for mod in config.get("imports", []):
        b.line(f"import {safe_identifier(mod)}")
    for mod, names in config.get("from_imports", {}).items():
        joined = ", ".join(safe_identifier(n) for n in names)
        b.line(f"from {safe_identifier(mod)} import {joined}")
    if config.get("imports") or config.get("from_imports"):
        b.blank()

    for name, value in config.get("constants", {}).items():
        b.line(f"{safe_identifier(name)} = {value!r}")
    if config.get("constants"):
        b.blank()

    for cls in config.get("classes", []):
        _emit_class(b, cls)
        b.blank()

    for fn in config.get("functions", []):
        _emit_function(b, fn)

    return b.render()


def _emit_class(b: CodeBuilder, cls: dict) -> None:
    name = safe_identifier(cls["name"])
    bases = ", ".join(safe_identifier(x) for x in cls.get("bases", []))
    header = f"class {name}({bases}):" if bases else f"class {name}:"
    with b.block(header, when=cls.get("when", True), on_empty="pass"):
        if cls.get("docstring"):
            b.line('"""' + str(cls["docstring"]).replace('"""', '\\"\\"\\"') + '"""')
        for attr, value in cls.get("attributes", {}).items():
            b.line(f"{safe_identifier(attr)} = {value!r}")
        for method in cls.get("methods", []):
            _emit_function(b, method, is_method=True)


def _emit_function(b: CodeBuilder, fn: dict, *, is_method: bool = False) -> None:
    name = safe_identifier(fn["name"])
    args = ", ".join(safe_identifier(a) for a in fn.get("args", []))
    for deco in fn.get("decorators", []):
        if fn.get("when", True):
            b.line(f"@{safe_identifier(deco)}")
    with b.block(f"def {name}({args}):", when=fn.get("when", True), on_empty="pass"):
        if fn.get("comment"):
            b.comment(fn["comment"])
        for stmt in fn.get("body", []):
            b.line(stmt)
    b.blank()


DEMO_CONFIG = {
    "module_doc": "自动生成的服务骨架。",
    "imports": ["json"],
    "from_imports": {"typing": ["Any", "Optional"]},
    "constants": {"DEFAULT_TIMEOUT": 30},
    "classes": [
        {
            "name": "Handler",
            "bases": ["object"],
            "docstring": "请求处理器。",
            "attributes": {"count": 0},
            "methods": [
                {"name": "handle", "args": ["self", "request"],
                 "comment": "入口方法",
                 "body": ["return self._dispatch(request)"]},
                {"name": "_dispatch", "args": ["self", "request"],
                 "body": ["return request"]},
                {"name": "disabled_feature", "args": ["self"], "when": False,
                 "body": ["raise NotImplementedError"]},
            ],
        },
        {"name": "Empty", "methods": []},  # 空类：块体省略后自动补 pass
    ],
    "functions": [
        {"name": "main", "args": [], "body": ["handler = Handler()", "return handler"]},
    ],
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="从 JSON 配置生成 Python 骨架")
    parser.add_argument("config", nargs="?", help="配置 JSON 路径（缺省用内置演示配置）")
    parser.add_argument("-o", "--output", help="输出文件（缺省打印到 stdout）")
    args = parser.parse_args(argv)

    if args.config:
        with open(args.config, encoding="utf-8") as fh:
            config = json.load(fh)
    else:
        config = DEMO_CONFIG

    source = generate(config)
    ast.parse(source)  # 生成结果必须能被目标语言解析器接受

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(source)
        print(f"written: {args.output} ({len(source)} bytes, "
              f"{source.count(chr(10))} lines)", file=sys.stderr)
    else:
        sys.stdout.write(source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
