#!/usr/bin/env python3
"""时钟使用检查工具：扫描"直接取系统时间 / 直接休眠"的调用点。

时钟抽象 src/clocks.py 提供三个接口：

- Clock.wall()       墙上时间（日历时刻，可能回拨）
- Clock.monotonic()  单调时间（间隔测量，不会回拨）
- Clock.sleep(secs)  可注入的睡眠（FakeClock 不真实等待）

业务实现必须通过注入的 Clock 取时/休眠。本工具用 AST 扫描，
精确报告以下直读/直睡调用（注释、文档字符串、字符串字面量不会误报）：

  墙上时间: time.time, time.ns/time_ns, datetime.datetime.now/today/utcnow,
            datetime.date.today, time.gmtime()/localtime() 无参形式
  单调时间: time.monotonic/_ns, time.perf_counter/_ns,
            time.process_time/_ns, time.thread_time/_ns, time.timeit
  直接休眠: time.sleep

退出码:
  0  未发现违规
  1  发现违规
  2  工具/配置错误（路径不存在、豁免项失效等）
"""

import argparse
import ast
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 仓库内"刻意保留"的豁免文件：相对仓库根的 POSIX 路径 + 原因。
# 新增豁免必须在此显式登记并写明理由，没有登记的豁免一律不算数。
BUILTIN_EXEMPT = {
    "src/clocks.py": "SystemClock 是时钟抽象的标准库适配层，是唯一允许直读/直睡的接缝",
    "src/legacy_timing.py": "重构前实现，刻意保留作为差分测试基准，不得再被生产代码引用",
}

RISK_WALL = (
    "直接读取墙上时间，NTP 校时/闰秒/人工改时间会使其回拨或跳变，"
    "超时与间隔判定不可预测；且无法注入 FakeClock，测试只能真实等待、不可复现"
)
RISK_MONO = (
    "直接读取单调时间，绕过时钟抽象，无法注入 FakeClock，测试只能真实等待、不可复现"
)
RISK_SLEEP = (
    "直接休眠会真实占用墙钟时间，测试变慢且不确定；FakeClock 无法推进它，"
    "退避序列无法确定性断言"
)

# (模块, 属性链) -> (类别, 建议接口, 风险说明)
ATTR_RULES = {
    ("time", "time"): ("wall", "clock.wall()", RISK_WALL),
    ("time", "time_ns"): ("wall", "clock.wall()", RISK_WALL + "；注意 *_ns 返回整数纳秒，改用后需换算为秒"),
    ("time", "gmtime"): ("wall", "clock.wall() 后自行 gmtime", RISK_WALL + "；仅无参（取当前时刻）形式违规"),
    ("time", "localtime"): ("wall", "clock.wall() 后自行 localtime", RISK_WALL + "；仅无参（取当前时刻）形式违规"),
    ("time", "monotonic"): ("mono", "clock.monotonic()", RISK_MONO),
    ("time", "monotonic_ns"): ("mono", "clock.monotonic()", RISK_MONO + "；注意 *_ns 返回整数纳秒，改用后需换算为秒"),
    ("time", "perf_counter"): ("mono", "clock.monotonic()", RISK_MONO + "；perf_counter 属于单调计时语义"),
    ("time", "perf_counter_ns"): ("mono", "clock.monotonic()", RISK_MONO + "；perf_counter 属于单调计时语义；注意 *_ns 返回整数纳秒"),
    ("time", "process_time"): ("mono", "clock.monotonic()", RISK_MONO + "；process_time 属于单调计时语义"),
    ("time", "process_time_ns"): ("mono", "clock.monotonic()", RISK_MONO + "；process_time 属于单调计时语义；注意 *_ns 返回整数纳秒"),
    ("time", "thread_time"): ("mono", "clock.monotonic()", RISK_MONO + "；thread_time 属于单调计时语义"),
    ("time", "thread_time_ns"): ("mono", "clock.monotonic()", RISK_MONO + "；thread_time 属于单调计时语义；注意 *_ns 返回整数纳秒"),
    ("time", "timeit"): ("mono", "clock.monotonic() 两次取值做差", RISK_MONO),
    ("time", "sleep"): ("sleep", "clock.sleep(seconds)", RISK_SLEEP),
    ("datetime", "datetime.now"): ("wall", "clock.wall() 后构造 datetime", RISK_WALL),
    ("datetime", "datetime.utcnow"): ("wall", "clock.wall() 后构造 datetime", RISK_WALL + "；utcnow 还是 Python 3.12 起弃用的朴素 UTC 接口"),
    ("datetime", "datetime.today"): ("wall", "clock.wall() 后构造 datetime", RISK_WALL),
    ("datetime", "date.today"): ("wall", "clock.wall() 后构造 date", RISK_WALL),
}

# from time import time / sleep / ... 直接名调用
NAME_RULES = {
    "time": ("wall", "clock.wall()", RISK_WALL),
    "time_ns": ("wall", "clock.wall()", RISK_WALL),
    "monotonic": ("mono", "clock.monotonic()", RISK_MONO),
    "monotonic_ns": ("mono", "clock.monotonic()", RISK_MONO),
    "perf_counter": ("mono", "clock.monotonic()", RISK_MONO),
    "perf_counter_ns": ("mono", "clock.monotonic()", RISK_MONO),
    "process_time": ("mono", "clock.monotonic()", RISK_MONO),
    "process_time_ns": ("mono", "clock.monotonic()", RISK_MONO),
    "thread_time": ("mono", "clock.monotonic()", RISK_MONO),
    "thread_time_ns": ("mono", "clock.monotonic()", RISK_MONO),
    "sleep": ("sleep", "clock.sleep(seconds)", RISK_SLEEP),
}

CATEGORY_LABEL = {"wall": "墙上时间直读", "mono": "单调时间直读", "sleep": "直接休眠"}


class Violation:
    __slots__ = ("path", "lineno", "col", "enclosing", "kind", "suggest", "risk")

    def __init__(self, path, lineno, col, enclosing, kind, suggest, risk):
        self.path = path
        self.lineno = lineno
        self.col = col
        self.enclosing = enclosing
        self.kind = kind
        self.suggest = suggest
        self.risk = risk


class ClockUsageVisitor(ast.NodeVisitor):
    """收集一个文件内所有直读/直睡调用点。"""

    def __init__(self, path):
        self.path = path
        self.violations = []
        # 导入别名绑定：本地名 -> (模块, 属性链)
        self._bindings = {}
        self._scope = []  # 函数/类名栈，用于定位调用点所在函数

    # ---- 导入绑定 -------------------------------------------------
    def visit_Import(self, node):
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self._bindings[local] = (alias.name, ())
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module is None:
            self.generic_visit(node)
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            local = alias.asname or alias.name
            self._bindings[local] = (node.module, (alias.name,))
        self.generic_visit(node)

    # ---- 作用域跟踪 -------------------------------------------------
    def _push(self, name):
        self._scope.append(name)

    def _pop(self):
        self._scope.pop()

    def visit_FunctionDef(self, node):
        self._push(node.name)
        self.generic_visit(node)
        self._pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._push(node.name)
        self.generic_visit(node)
        self._pop()

    # ---- 调用点匹配 -------------------------------------------------
    def visit_Call(self, node):
        rule = self._match(node.func)
        if rule is not None:
            kind, suggest, risk = rule
            # gmtime()/localtime() 仅无参形式是"取当前时刻"，带参是格式转换，不违规
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("gmtime", "localtime"):
                if node.args or node.keywords:
                    rule = None
            if rule is not None:
                self.violations.append(Violation(
                    path=self.path,
                    lineno=node.lineno,
                    col=node.col_offset + 1,
                    enclosing=".".join(self._scope) or "<module>",
                    kind=kind,
                    suggest=suggest,
                    risk=risk,
                ))
        self.generic_visit(node)

    def _match(self, func):
        """返回 (类别, 建议, 风险) 或 None。"""
        # 形式一：time.time() / datetime.datetime.now()（含 import ... as 别名）
        if isinstance(func, ast.Attribute):
            chain = _attribute_chain(func)
            if chain is not None:
                root = chain[0]
                module, extra = self._bindings.get(root, (root, ()))
                full = (module,) + extra + tuple(chain[1:])
                rule = ATTR_RULES.get(full)
                if rule is not None:
                    return rule
        # 形式二：from time import time; time()
        if isinstance(func, ast.Name):
            bound = self._bindings.get(func.id)
            if bound is not None:
                module, extra = bound
                if module == "time" and extra and extra[0] in NAME_RULES:
                    return NAME_RULES[extra[0]]
        return None


def _attribute_chain(node):
    """把 a.b.c 解析成 ('a','b','c')；含下标/调用的形式返回 None。"""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return tuple(reversed(parts))
    return None


def scan_paths(paths, exempt):
    """扫描给定文件/目录，返回 (违规列表, 失效豁免列表)。"""
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
                files += [os.path.join(root, n) for n in sorted(names) if n.endswith(".py")]
        elif p.endswith(".py"):
            files.append(p)
    violations = []
    stale = []
    for fpath in sorted(files):
        rel = os.path.relpath(fpath, REPO_ROOT).replace(os.sep, "/")
        if rel in exempt:
            continue
        with open(fpath, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=fpath)
        visitor = ClockUsageVisitor(rel)
        visitor.visit(tree)
        violations.extend(visitor.violations)
    for rel in exempt:
        if not os.path.exists(os.path.join(REPO_ROOT, rel)):
            stale.append(rel)
    return violations, stale


def format_report(violations, stale, exempt):
    lines = []
    for v in violations:
        lines.append("%s:%d:%d: [%s] %s" % (v.path, v.lineno, v.col,
                                            CATEGORY_LABEL[v.kind], v.enclosing))
        lines.append("    建议替换: %s" % v.suggest)
        lines.append("    风险说明: %s" % v.risk)
    for rel in stale:
        lines.append("%s: 豁免项已失效（文件不存在），请从豁免登记中删除" % rel)
    if exempt and not violations and not stale:
        lines.append("已豁免文件（%d 个，均经显式登记）:" % len(exempt))
        for rel in sorted(exempt):
            lines.append("  - %s —— %s" % (rel, exempt[rel]))
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description="扫描直接取系统时间/直接休眠的违规调用点")
    parser.add_argument("paths", nargs="*", default=["src"],
                        help="待扫描的文件或目录（默认 src；测试/工具目录刻意不纳入生产扫描）")
    args = parser.parse_args(argv)

    missing = [p for p in args.paths if not os.path.exists(p)]
    if missing:
        for p in missing:
            print("错误: 路径不存在: %s" % p, file=sys.stderr)
        return 2

    violations, stale = scan_paths(args.paths, BUILTIN_EXEMPT)
    report = format_report(violations, stale, BUILTIN_EXEMPT)

    if stale:
        for line in report:
            print(line, file=sys.stderr)
        return 2
    if violations:
        print("发现 %d 处时钟使用违规（必须改用注入的 Clock）:\n" % len(violations))
        for line in report:
            print(line)
        return 1
    print("PASS: 未发现直接取系统时间/直接休眠的调用点")
    for line in report:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
