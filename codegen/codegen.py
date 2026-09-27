"""结构化代码生成核心库（仅标准库）。

设计要点：
- 缩进完全由 block() 作用域的嵌套结构决定，调用方只写逻辑行，不手写空格。
- 空块整体省略：块内没有产生任何非空行时，块头连同块体一起删除，
  不会留下空行或悬挂缩进；也可选择用占位语句（如 pass）填充。
- 确定性：不依赖 dict/set 的迭代顺序（set 会被排序），不依赖时间戳、
  对象 id 或哈希随机化，同一配置多次生成逐字节一致。
"""

from __future__ import annotations

import keyword
import re
from contextlib import contextmanager

_MAX_BLANK_RUN = 2  # 渲染时连续空行折叠上限


def safe_identifier(name, *, _invalid=re.compile(r"\W")):
    """把任意配置字符串确定性地映射为合法的 Python 标识符。

    规则（全部为纯函数，无随机性）：
    1. 非 [a-zA-Z0-9_] 字符替换为 '_'；
    2. 空串或以数字开头则前缀 '_'；
    3. 与 Python 硬/软关键字冲突则后缀 '_'。
    """
    ident = _invalid.sub("_", str(name))
    if not ident or ident[0].isdigit():
        ident = "_" + ident
    if keyword.iskeyword(ident):
        ident += "_"
    elif ident in keyword.softkwlist and ident != "_":
        ident += "_"
    return ident


class CodeBuilder:
    """带作用域的代码块构建器。

    用法::

        b = CodeBuilder()
        b.line("import os")
        with b.block("def main():"):
            b.line("return 0")
        text = b.render()
    """

    def __init__(self, indent_unit: str = "    "):
        if not indent_unit or indent_unit.strip(" \t"):
            raise ValueError("indent_unit 只能由空格或制表符组成")
        self.indent_unit = indent_unit
        self._lines = []       # list[str]，已带缩进的逻辑行（空行为 ""）
        self._depth = 0
        self._suppressed = 0   # >0 时所有写入被丢弃（用于 when=False）

    # ------------------------------------------------------------------ 基础写入

    def line(self, text: str = "") -> None:
        """写入一行；text 为 "" 时写入空行。多行文本按行拆分。"""
        if self._suppressed:
            return
        for part in str(text).splitlines() or [""]:
            self._lines.append(self.indent_unit * self._depth + part if part else "")

    def blank(self) -> None:
        """写入一个空行。"""
        self.line("")

    def comment(self, text: str) -> None:
        """写入注释行（自动加 '# ' 前缀，支持多行）。"""
        for part in str(text).splitlines() or [""]:
            self.line("# " + part if part else "#")

    # ------------------------------------------------------------------ 作用域块

    @contextmanager
    def block(self, header=None, *, when=True, on_empty="omit", trailer=None):
        """进入一个缩进作用域。

        header    : 块头行（如 "def f():"），None 表示匿名块（只增加缩进）。
        when      : False 时整个块（含块头与块体）不生成。
        on_empty  : 块体为空时的策略：
                    "omit" 整块删除（默认）；"pass" 填入占位语句；
                    "keep" 保留块头（调用方需自行保证语法合法）。
        trailer   : 块结束后追加的 dedent 行（如 C 风格的 "}"）。
        """
        if not when:
            self._suppressed += 1
            try:
                yield self
            finally:
                self._suppressed -= 1
            return

        if on_empty not in ("omit", "pass", "keep"):
            raise ValueError("on_empty 必须是 'omit' / 'pass' / 'keep'")

        start = len(self._lines)
        if header is not None:
            self.line(header)
        self._depth += 1
        try:
            yield self
        finally:
            body = self._lines[start + (1 if header is not None else 0):]
            if header is not None and not any(body):
                if on_empty == "omit":
                    del self._lines[start:]
                elif on_empty == "pass":
                    del self._lines[start + 1:]
                    self.line("pass")  # 此时深度尚未回退，pass 落在块内
            self._depth -= 1
            if trailer is not None and len(self._lines) > start:
                self.line(trailer)

    # ------------------------------------------------------------------ 组合生成

    def when(self, condition: bool, fn) -> bool:
        """条件生成：condition 为真时执行 fn(self)。返回 condition。"""
        if condition:
            fn(self)
        return bool(condition)

    def for_each(self, items, fn) -> None:
        """循环生成：对 items 中每项调用 fn(self, item)。

        传入 set/frozenset 时先按 repr 排序，保证与哈希随机化无关的确定顺序。
        """
        if isinstance(items, (set, frozenset)):
            items = sorted(items, key=repr)
        for item in items:
            fn(self, item)

    # ------------------------------------------------------------------ 渲染

    def render(self) -> str:
        """渲染为最终文本。

        归一化规则（均为确定性操作）：去掉首尾的空白行；连续空行折叠到
        最多 _MAX_BLANK_RUN 行；文件以恰好一个换行符结尾。
        """
        lines = list(self._lines)
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        out, blanks = [], 0
        for ln in lines:
            if ln:
                blanks = 0
                out.append(ln)
            else:
                blanks += 1
                if blanks <= _MAX_BLANK_RUN:
                    out.append(ln)
        return "\n".join(out) + "\n" if out else ""

    def __len__(self):
        return len(self._lines)
