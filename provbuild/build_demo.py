"""A small simulated build system wired to ProvenanceGraph.

Pipeline per module i:
  src/mod_i.c  --(cc)-->  build/mod_i.o      (compile; every compile also
  include/common.h --^                       consumes the shared header)
  build/mod_*.o  --(ld)-->  build/app        (link; generated files re-consumed)

This exercises: one source shared by many artifacts, generated files
consumed again downstream, and tool identity (name/version/argv) capture.
"""

from __future__ import annotations

import os
import sys
from typing import List, Tuple

from .provenance import ProvenanceGraph, Tool

CC = Tool(name="cc", version="13.2.0", argv=("-O2", "-c"))
LD = Tool(name="ld", version="2.41", argv=("--gc-sections",))


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def fake_cc(tool: Tool, inputs: List[str], output: str) -> None:
    body = "\n".join(_read(p) for p in inputs)
    _write(output, f"# {tool.name} {tool.version} {' '.join(tool.argv)}\n{body.upper()}")


def fake_ld(tool: Tool, inputs: List[str], output: str) -> None:
    parts = []
    for p in inputs:
        parts.append(_read(p))
    _write(output, f"# {tool.name} {tool.version} {' '.join(tool.argv)}\n" + "\n".join(parts))


def run_build(root: str, n_modules: int, record: bool = True) -> Tuple[ProvenanceGraph, str]:
    """Run the simulated build under `root`; return (graph, final_binary).

    With record=False, skip provenance recording (baseline for overhead
    measurement); the returned graph is empty."""
    src_dir = os.path.join(root, "src")
    inc_dir = os.path.join(root, "include")
    obj_dir = os.path.join(root, "build")
    os.makedirs(src_dir, exist_ok=True)
    os.makedirs(inc_dir, exist_ok=True)
    os.makedirs(obj_dir, exist_ok=True)

    graph = ProvenanceGraph()

    header = os.path.join(inc_dir, "common.h")
    _write(header, "#define VERSION 1\n")
    if record:
        graph.add_source(header)

    objects: List[str] = []
    for i in range(n_modules):
        src = os.path.join(src_dir, f"mod_{i}.c")
        _write(src, f"int fn_{i}(void) {{ return {i}; }}\n")
        obj = os.path.join(obj_dir, f"mod_{i}.o")
        fake_cc(CC, [src, header], obj)
        if record:
            graph.add_source(src)
            graph.record_step(
                step_id=f"compile-{i}",
                tool=CC,
                inputs=[src, header],   # shared header consumed by every module
                outputs=[obj],
            )
        objects.append(obj)

    app = os.path.join(obj_dir, "app")
    fake_ld(LD, objects, app)
    if record:
        graph.record_step(
            step_id="link-app",
            tool=LD,
            inputs=objects,           # generated intermediates re-consumed
            outputs=[app],
        )
        graph.declare_outputs([app])
    return graph, app


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "demo_out"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    graph, app = run_build(root, n)
    errors = graph.validate()
    prov_path = os.path.join(root, "provenance.json")
    size = graph.save(prov_path)
    print(f"built {app}")
    print(f"validate: {'OK' if not errors else errors}")
    print(f"provenance: {prov_path} ({size} bytes)")
    report = graph.check_replay(app)
    print(report.summary())
