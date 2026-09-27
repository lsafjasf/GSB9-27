"""Deterministic, structure-driven Python code generator (stdlib only).

Indentation is derived from the block tree, never from caller-supplied
whitespace. Rendering the same tree always produces byte-identical output.
"""

from __future__ import annotations

import keyword
from contextlib import contextmanager

__all__ = ["CodeGen", "safe_ident"]


def safe_ident(name):
    """Map an arbitrary string to a valid, keyword-safe Python identifier.

    Deterministic: the same input always yields the same output.

    - characters that are not alphanumeric or _ become _
    - a leading digit gets a _ prefix
    - empty results become _
    - Python keywords / soft keywords get a _ suffix
    """
    cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in name)
    if not cleaned:
        cleaned = "_"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    if cleaned != "_" and (
            keyword.iskeyword(cleaned) or keyword.issoftkeyword(cleaned)):
        cleaned += "_"
    return cleaned


class _Line:
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text


class _Blank:
    __slots__ = ()


class _Block:
    __slots__ = ("header", "children", "on_empty")

    def __init__(self, header, on_empty):
        self.header = header
        self.children = []
        self.on_empty = on_empty


class CodeGen:
    """Scoped code-block builder.

    indent: one indentation unit (default four spaces), whitespace only.
    on_empty: default policy for blocks with no emitted body lines;
    "omit" drops the block (header included), "pass" emits a pass body.
    """

    def __init__(self, indent="    ", on_empty="omit"):
        if not indent or indent.strip(" \t"):
            raise ValueError("indent must be a non-empty whitespace string")
        if on_empty not in ("omit", "pass"):
            raise ValueError("on_empty must be 'omit' or 'pass'")
        self._indent = indent
        self._on_empty = on_empty
        self._root = _Block(None, "omit")
        self._stack = [self._root]

    def line(self, text=""):
        """Append one logical line at the current scope. Empty text = blank."""
        if text == "":
            return self.blank()
        if "\n" in text or "\r" in text:
            raise ValueError("line() takes a single line; use one call per line")
        self._stack[-1].children.append(_Line(text.rstrip()))
        return self

    def blank(self):
        """Append a blank separator line (renders as a truly empty line)."""
        self._stack[-1].children.append(_Blank())
        return self

    def comment(self, text):
        """Append one or more # comment lines at the current scope."""
        parts = text.splitlines() or [""]
        for part in parts:
            self.line("# " + part if part.strip() else "#")
        return self

    @contextmanager
    def block(self, header, *, on_empty=None):
        """Open an indented scope introduced by header (e.g. def f():)."""
        if "\n" in header or "\r" in header:
            raise ValueError("block header must be a single line")
        node = _Block(header.rstrip(), on_empty or self._on_empty)
        self._stack[-1].children.append(node)
        self._stack.append(node)
        try:
            yield self
        finally:
            popped = self._stack.pop()
            assert popped is node, "unbalanced block nesting"

    @contextmanager
    def when(self, condition, header=None, *, on_empty=None):
        """Conditionally emit content.

        condition false -> everything emitted inside is discarded.
        condition true with header -> content wrapped in a block.
        condition true without header -> content inlined at this scope.
        """
        if condition and header is not None:
            with self.block(header, on_empty=on_empty):
                yield self
        elif condition:
            yield self
        else:
            sink = _Block(None, "omit")  # detached: swallows emissions
            self._stack.append(sink)
            try:
                yield self
            finally:
                self._stack.pop()

    def for_each(self, items, body, *, header=None, sort_key=None):
        """Emit one block per item (loop generation).

        body(gen, item) is called inside the item's scope. header is a
        callable mapping item -> header line; omit it to inline items at the
        current scope. sort_key sorts items first, giving deterministic
        output even when items is an unordered container (set, dict keys).
        """
        seq = list(items)
        if sort_key is not None:
            seq.sort(key=sort_key)
        for item in seq:
            if header is None:
                body(self, item)
            else:
                with self.block(header(item)):
                    body(self, item)
        return self

    def render_lines(self):
        """Render to a list of lines (no trailing newline characters)."""
        out = []
        self._render_children(self._root, 0, out)
        while out and out[-1] == "":
            out.pop()
        return out

    def render(self):
        """Render to text. Ends with exactly one newline; empty gen -> ''."""
        lines = self.render_lines()
        return "\n".join(lines) + "\n" if lines else ""

    def _render_children(self, node, depth, out):
        for child in node.children:
            if isinstance(child, _Line):
                out.append(self._indent * depth + child.text)
            elif isinstance(child, _Blank):
                out.append("")
            else:
                self._render_block(child, depth, out)

    def _render_block(self, node, depth, out):
        buf = []
        self._render_children(node, depth + 1, buf)
        if not buf or all(line == "" for line in buf):
            # Empty (or blank-only) block: omit wholesale, or emit a pass body.
            if node.on_empty == "omit":
                return
            out.append(self._indent * depth + node.header)
            out.append(self._indent * (depth + 1) + "pass")
            return
        out.append(self._indent * depth + node.header)
        out.extend(buf)
