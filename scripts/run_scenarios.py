"""Scenario harness: applies a change set to a pristine copy of the repo,
runs full + incremental + compare, and writes results/*.txt plus RESULTS.md.

Usage: python3 scripts/run_scenarios.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(REPO_ROOT, "results")
TOTAL_CHECKS = 5

CALCULATOR_V2 = '''"""Tiny calculator module used by the demo checks."""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        raise ValueError("division by zero")
    return a / b
'''

TEST_CALCULATOR_V2 = '''import unittest

import calculator


class CalculatorTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(calculator.add(2, 3), 5)

    def test_subtract(self):
        self.assertEqual(calculator.subtract(10, 4), 6)

    def test_multiply(self):
        self.assertEqual(calculator.multiply(3, 7), 21)

    def test_multiply_by_zero(self):
        self.assertEqual(calculator.multiply(5, 0), 0)


if __name__ == "__main__":
    unittest.main()
'''

TEST_CALCULATOR_BROKEN = TEST_CALCULATOR_V2.replace(
    "self.assertEqual(calculator.multiply(5, 0), 0)",
    "self.assertEqual(calculator.multiply(5, 0), 1)",
)

CALCULATOR_BAD_STYLE = CALCULATOR_V2 + "\nCONSTANT_WITH_A_VERY_LONG_LINE = " + repr("x" * 120) + "\n"

STRINGS_BAD_SYNTAX = '''"""String helpers used by the demo checks."""


def shout(text):
    return text.upper() + "!"


def slugify(text)
    return "-".join(text.lower().split())
'''

USERS_V2 = json.dumps(
    [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}, {"id": 3, "name": "Carol"}],
    indent=2,
) + "\n"

USERS_BROKEN = '[{"id": 1, "name": "Alice"},\n'

# Each scenario: (name, description, [(relpath, content), ...])
SCENARIOS = [
    (
        "single-src-file",
        "单文件改动：修改一个源码文件",
        [("demo/src/calculator.py", CALCULATOR_V2)],
    ),
    (
        "single-test-file",
        "单文件改动：修改一个测试文件",
        [("demo/tests/test_calculator.py", TEST_CALCULATOR_V2)],
    ),
    (
        "single-data-file",
        "单文件改动：修改一个 JSON 数据文件",
        [("demo/data/users.json", USERS_V2)],
    ),
    (
        "multi-file",
        "多文件改动：源码 + 数据 + 文档",
        [
            ("demo/src/calculator.py", CALCULATOR_V2),
            ("demo/data/users.json", USERS_V2),
            ("README.md", "# GSB9-27\n\nincremental precheck demo\n"),
        ],
    ),
    (
        "docs-only",
        "仅文档改动（命中忽略规则，零检查）",
        [("README.md", "# GSB9-27\n\ndocs only change\n")],
    ),
    (
        "config-change",
        "配置文件改动：precheck.json 变更，必须回退全量",
        [("precheck.json", None)],  # special: mutate config below
    ),
    (
        "unknown-file",
        "无法推断：新增映射之外的文件类型，必须回退全量",
        [("demo/assets/logo.png", None)],  # special: binary write below
    ),
    (
        "fault-style",
        "故障注入：源码引入超长行，py-style 必须 FAIL 且两种模式输出一致",
        [("demo/src/calculator.py", CALCULATOR_BAD_STYLE)],
    ),
    (
        "fault-syntax",
        "故障注入：源码引入语法错误，py-syntax 必须 FAIL 且两种模式输出一致",
        [("demo/src/strings.py", STRINGS_BAD_SYNTAX)],
    ),
    (
        "fault-test",
        "故障注入：测试断言错误，py-tests 必须 FAIL 且两种模式输出一致",
        [("demo/tests/test_calculator.py", TEST_CALCULATOR_BROKEN)],
    ),
    (
        "fault-json",
        "故障注入：JSON 数据损坏，json-validate 必须 FAIL 且两种模式输出一致",
        [("demo/data/users.json", USERS_BROKEN)],
    ),
]


def copy_repo(dst):
    shutil.copytree(os.path.join(REPO_ROOT, "demo"), os.path.join(dst, "demo"))
    shutil.copy2(os.path.join(REPO_ROOT, "precheck.json"), os.path.join(dst, "precheck.json"))


def apply_mutation(root, name, writes):
    changed = []
    for rel, content in writes:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if name == "config-change":
            with open(path, "r", encoding="utf-8") as handle:
                cfg = json.load(handle)
            cfg["ignore_patterns"].append("**.txt")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(cfg, handle, indent=2)
        elif name == "unknown-file":
            with open(path, "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\n" + bytes(range(64)))
        else:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
        changed.append(rel)
    return changed


def run_cli(root, *cli_args):
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "precheck.cli", *cli_args, "--root", root],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )
    wall_ms = (time.perf_counter() - started) * 1000.0
    return proc, wall_ms


def parse_field(output, prefix):
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    rows = []
    details_md = []
    for name, description, writes in SCENARIOS:
        with tempfile.TemporaryDirectory(prefix=f"precheck-{name}-") as tmp:
            copy_repo(tmp)
            changed = apply_mutation(tmp, name, writes)

            full_proc, full_wall = run_cli(tmp, "run", "--full")
            incr_proc, incr_wall = run_cli(tmp, "run", "--changed", *changed)
            cmp_proc, _ = run_cli(tmp, "compare", "--changed", *changed)

        slug = name
        with open(os.path.join(RESULTS_DIR, f"{slug}.full.txt"), "w") as fh:
            fh.write(full_proc.stdout)
        with open(os.path.join(RESULTS_DIR, f"{slug}.incr.txt"), "w") as fh:
            fh.write(incr_proc.stdout)
        with open(os.path.join(RESULTS_DIR, f"{slug}.compare.txt"), "w") as fh:
            fh.write(cmp_proc.stdout)

        incr_out = incr_proc.stdout
        mode = parse_field(incr_out, "mode:")
        affected = parse_field(incr_out, "affected:")
        n_run = int(parse_field(incr_out, "checks-run:") or 0)
        full_ms = float(parse_field(full_proc.stdout, "total-check-ms:") or 0)
        incr_ms = float(parse_field(incr_out, "total-check-ms:") or 0)
        consistent = "result: CONSISTENT" in cmp_proc.stdout
        verdict_full = parse_field(full_proc.stdout, "verdict:").split()[0]
        verdict_incr = parse_field(incr_out, "verdict:").split()[0]
        hit_rate = f"{n_run}/{TOTAL_CHECKS}"
        saved = (1 - incr_ms / full_ms) * 100 if full_ms > 0 else 0.0
        rows.append(
            (
                name,
                mode,
                hit_rate,
                f"{full_ms:.1f}",
                f"{incr_ms:.1f}",
                f"{saved:.0f}%",
                f"{verdict_full}/{verdict_incr}",
                "OK" if consistent else "MISMATCH",
            )
        )
        details_md.append(
            f"### {name}\n\n{description}\n\n"
            f"- changed: `{', '.join(changed)}`\n"
            f"- mode: `{mode}`, affected: `{affected or '(none)'}`\n"
            f"- verdict full/incr: {verdict_full}/{verdict_incr}, "
            f"compare: {'CONSISTENT' if consistent else 'INCONSISTENT'}\n"
            f"- logs: `results/{slug}.full.txt`, `results/{slug}.incr.txt`, "
            f"`results/{slug}.compare.txt`\n"
        )
        print(f"[{name}] mode={mode} run={hit_rate} consistent={consistent}")

    header = (
        "| 场景 | 模式 | 命中率(执行/总数) | 全量耗时ms | 增量耗时ms | 节省 | 判定(全量/增量) | 一致性 |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )
    table = "".join("| " + " | ".join(row) + " |\n" for row in rows)
    doc = (
        "# 增量 vs 全量 对比数据\n\n"
        "由 `python3 scripts/run_scenarios.py` 自动生成。"
        "每个场景在仓库的干净临时副本上应用改动，分别执行全量与增量，"
        "再用 `compare` 子命令逐检查项对比（状态 + 明细，忽略耗时）。\n\n"
        "## 汇总\n\n"
        + header
        + table
        + "\n> 说明：演示项目的检查本身在毫秒级，真实仓库中单项检查耗时以分钟计时，"
        "命中率直接等比映射为节省时间。`compare` 的一致性判定不比较耗时字段。\n\n"
        "## 场景明细\n\n"
        + "\n".join(details_md)
    )
    with open(os.path.join(REPO_ROOT, "RESULTS.md"), "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"\nwrote RESULTS.md and {len(SCENARIOS) * 3} log files under results/")
    return 0 if all(row[-1] == "OK" for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
