#!/usr/bin/env python3
"""Generate a deterministic synthetic ~100k-line Python codebase for
performance measurement. Usage: python3 benchmark/gen_corpus.py [outdir]
"""
import os
import random
import sys

FILES = 400          # 400 modules
FUNCS_PER_FILE = 25  # ~10k functions total


def gen_function(rng, idx):
    lines = [f"def func_{idx}(a, b, c=None):"]
    n_stmt = rng.randint(3, 12)
    for i in range(n_stmt):
        kind = rng.random()
        if kind < 0.35:
            lines.append(f"    v{i} = a + {i}")
        elif kind < 0.55:
            lines.append(f"    if a > {i}:")
            lines.append(f"        v{i} = b")
            if rng.random() < 0.4:
                lines.append(f"    elif a < -{i}:")
                lines.append(f"        v{i} = -b")
            else:
                lines.append(f"    else:")
                lines.append(f"        v{i} = 0")
        elif kind < 0.7:
            lines.append(f"    for i_{i} in range({rng.randint(2, 9)}):")
            lines.append(f"        a += i_{i}")
        elif kind < 0.8:
            lines.append(f"    while a > {i}:")
            lines.append(f"        a -= 1")
        elif kind < 0.9:
            lines.append(f"    try:")
            lines.append(f"        v{i} = int(a)")
            lines.append(f"    except ValueError:")
            lines.append(f"        v{i} = 0")
        else:
            lines.append(f"    v{i} = a and b or c")
    lines.append("    return a")
    return "\n".join(lines)


def gen_module(rng, modidx, n_mods):
    parts = [f'"""Synthetic module {modidx}."""']
    for _ in range(rng.randint(0, 4)):
        parts.append(f"import mod_{rng.randrange(n_mods):04d}")
    for fidx in range(FUNCS_PER_FILE):
        parts.append(gen_function(rng, modidx * FUNCS_PER_FILE + fidx))
        parts.append("")
    return "\n".join(parts) + "\n"


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "corpus")
    os.makedirs(outdir, exist_ok=True)
    rng = random.Random(20260928)
    total = 0
    for i in range(FILES):
        src = gen_module(rng, i, FILES)
        total += src.count("\n")
        with open(os.path.join(outdir, f"mod_{i:04d}.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(src)
    print(f"wrote {FILES} files, {total} lines -> {outdir}")


if __name__ == "__main__":
    main()
