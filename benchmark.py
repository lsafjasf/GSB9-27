#!/usr/bin/env python3
"""benchmark.py — 生成约 10 万行的合成代码库并测量检测耗时与种植克隆召回率。

合成方法: 从参数化语句片段池中拼装函数; 大多数函数随机组合(互不相同),
另按固定种子种植克隆对(重命名 / 重命名+换序 / 部分重叠), 用于验证召回。
"""
import json
import os
import random
import shutil
import sys
import time

import clone_detector as cd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "benchmark_src")
LABELS = os.path.join(HERE, "benchmark_labels.json")

TARGET_LOC = 100_000
FUNCS_PER_FILE = 10
PLANTED_CLONES = 400  # 种植的克隆对数


def snippet_pool():
    """参数化语句片段池: {p}=标识符前缀, {n}=数字字面量。

    从 (初始化 x 循环头 x 守卫 x 更新) 中确定性采样约 300 个片段。
    各维度在归一化后仍有 ~90 种不同的行模式, 保证随机拼装的函数
    之间相似度远低于阈值, 只有显式种植的克隆对才应被检出。
    合成代码只要求能解析, 不要求可运行。
    """
    rng = random.Random(99)
    inits = [
        "acc_{p} = 0",
        "acc_{p} = []",
        "acc_{p} = set()",
        "acc_{p} = {{}}",
        "acc_{p} = ''",
        "acc_{p} = 1",
        "acc_{p} = 0.0",
        "acc_{p} = None",
        "acc_{p} = [0] * {n}",
        "acc_{p} = {{'total': 0}}",
        "acc_{p} = ()",
        "acc_{p} = False",
    ]
    loops = [
        "for item_{p} in data_{p}:",
        "for idx_{p}, item_{p} in enumerate(data_{p}):",
        "for item_{p} in data_{p}[1:]:",
        "for item_{p} in reversed(data_{p}):",
        "for item_{p} in sorted(data_{p}):",
        "for item_{p} in data_{p}[::{n}]:",
        "for item_{p} in data_{p}.split(','):",
        "for item_{p} in zip(data_{p}, data_{p}):",
        "for item_{p} in range(len(data_{p})):",
        "for key_{p}, item_{p} in data_{p}.items():",
        "for item_{p} in filter(None, data_{p}):",
    ]
    guards = [
        None,
        "if item_{p} % {n} == 0:",
        "if item_{p}:",
        "if isinstance(item_{p}, int):",
        "if isinstance(item_{p}, str):",
        "if item_{p} not in acc_{p}:",
        "if item_{p} > {n}:",
        "if item_{p} is not None:",
        "if len(item_{p}) > {n}:",
        "if str(item_{p}).startswith('a'):",
        "if idx_{p} % 2 == 0:",
        "if item_{p} != acc_{p}:",
    ]
    updates = [
        "acc_{p} = acc_{p} + item_{p} * {n}",
        "acc_{p} = acc_{p} - item_{p} // {n}",
        "acc_{p} = acc_{p} * item_{p} - {n}",
        "acc_{p} = acc_{p} | item_{p} & {n}",
        "acc_{p} = acc_{p} ^ item_{p} << 1",
        "acc_{p} = (acc_{p} + item_{p}) % {n}",
        "acc_{p} = max(acc_{p}, item_{p})",
        "acc_{p} = min(acc_{p}, item_{p} + {n})",
        "acc_{p} = item_{p} if item_{p} > acc_{p} else acc_{p}",
        "acc_{p}.append(item_{p} * {n})",
        "acc_{p}.append((idx_{p}, item_{p}))",
        "acc_{p}.append(str(item_{p}))",
        "acc_{p}.extend(item_{p})",
        "acc_{p}.insert(0, item_{p})",
        "acc_{p}.add(item_{p})",
        "acc_{p}.add(item_{p} % {n})",
        "acc_{p}.update(item_{p})",
        "acc_{p}[item_{p}] = acc_{p}.get(item_{p}, 0) + {n}",
        "acc_{p}[item_{p}] = idx_{p}",
        "acc_{p}[item_{p} % {n}] = item_{p}",
        "acc_{p}.setdefault(item_{p}, []).append(idx_{p})",
        "acc_{p} += str(item_{p}) + ','",
        "acc_{p} += item_{p}[::-1]",
        "acc_{p} = acc_{p} + [item_{p}]",
        "acc_{p} = [x_{p} for x_{p} in item_{p}]",
        "acc_{p} = sorted(acc_{p} + [item_{p}])",
        "acc_{p}.append(item_{p}.strip())",
        "acc_{p}.append(len(item_{p}))",
        "acc_{p} = acc_{p} or item_{p}",
        "acc_{p} = acc_{p} and item_{p}",
    ]
    pool, seen = [], set()
    while len(pool) < 300:
        key = (rng.choice(inits), rng.choice(loops), rng.choice(guards), rng.choice(updates))
        if key in seen:
            continue
        seen.add(key)
        init, loop, guard, update = key
        snip = [init, loop]
        if guard:
            snip.append("    " + guard)
            snip.append("        " + update)
        else:
            snip.append("    " + update)
        pool.append(snip)

    # 少量多行特殊结构, 增加代码库真实感
    pool.append([
        "try:",
        "    value_{p} = int(data_{p}) * {n}",
        "except ValueError:",
        "    value_{p} = {n}",
    ])
    pool.append([
        "seen_{p} = set()",
        "while data_{p}:",
        "    node_{p} = data_{p}.pop()",
        "    if node_{p} not in seen_{p}:",
        "        seen_{p}.add(node_{p})",
    ])
    pool.append([
        "for i_{p} in range(len(data_{p})):",
        "    for j_{p} in range(len(data_{p}[i_{p}])):",
        "        data_{p}[i_{p}][j_{p}] += {n}",
    ])
    pool.append([
        "ordered_{p} = sorted(data_{p}, key=lambda x_{p}: x_{p}[0], reverse=True)",
        "top_{p} = ordered_{p}[:{n}]",
    ])
    pool.append([
        "if score_{p} >= {n}:",
        "    grade_{p} = 'high'",
        "elif score_{p} >= {n}:",
        "    grade_{p} = 'mid'",
        "else:",
        "    grade_{p} = 'low'",
    ])
    pool.append([
        "window_{p} = data_{p}[:{n}]",
        "acc_{p} = sum(window_{p})",
        "for k_{p} in range({n}, len(data_{p})):",
        "    acc_{p} += data_{p}[k_{p}] - data_{p}[k_{p} - {n}]",
    ])
    pool.append([
        "stack_{p} = []",
        "for tok_{p} in data_{p}:",
        "    if tok_{p} == '(':",
        "        stack_{p}.append(tok_{p})",
        "    elif tok_{p} == ')' and stack_{p}:",
        "        stack_{p}.pop()",
    ])
    pool.append([
        "buf_{p} = bytearray()",
        "for chunk_{p} in data_{p}:",
        "    buf_{p}.extend(chunk_{p})",
        "    if len(buf_{p}) > {n}:",
        "        break",
    ])
    return pool


RETURNS = [
    "return acc_{p}",
    "return len(acc_{p})",
    "return acc_{p} if acc_{p} else None",
    "return sorted(acc_{p})",
    "return list(acc_{p})",
    "return acc_{p}, data_{p}",
]


def render_function(name, prefix, snippets, rng):
    lines = ["def %s(data_%s, config_%s):" % (name, prefix, prefix)]
    for snip in snippets:
        for line in snip:
            lines.append("    " + line.format(p=prefix, n=rng.randint(2, 97)))
        lines.append("")
    lines.append("    " + rng.choice(RETURNS).format(p=prefix))
    return lines


def generate():
    rng = random.Random(20260928)
    pool = snippet_pool()
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR)

    # 预生成种植克隆对: 同一片段序列, 不同标识符前缀/字面量, 部分换序或增删
    planted = []  # (func_a, func_b)
    clone_funcs = {}
    for i in range(PLANTED_CLONES):
        snippets = [rng.choice(pool) for _ in range(rng.randint(4, 6))]
        kind = i % 3
        snip_b = list(snippets)
        if kind == 1:
            rng.shuffle(snip_b)                      # 重命名 + 换序
        elif kind == 2:
            snip_b = snip_b[:-1] + [rng.choice(pool)]  # 部分重叠
        name_a, name_b = "planted_%04d_a" % i, "planted_%04d_b" % i
        clone_funcs[name_a] = render_function(name_a, "pa%d" % i, snippets, rng)
        clone_funcs[name_b] = render_function(name_b, "pb%d" % i, snip_b, rng)
        planted.append((name_a, name_b))

    # 估计单函数行数, 计算需要的普通函数数量
    sample = render_function("f", "x", [rng.choice(pool) for _ in range(5)], rng)
    avg_lines = len(sample) + 1
    n_clone = len(clone_funcs)
    n_plain = max(0, TARGET_LOC // avg_lines - n_clone)

    func_names = ["plain_%06d" % i for i in range(n_plain)] + sorted(clone_funcs)
    rng.shuffle(func_names)

    loc = 0
    file_idx = 0
    for start in range(0, len(func_names), FUNCS_PER_FILE):
        chunk = func_names[start : start + FUNCS_PER_FILE]
        lines = []
        for name in chunk:
            if name in clone_funcs:
                lines += clone_funcs[name]
            else:
                snippets = [rng.choice(pool) for _ in range(rng.randint(4, 7))]
                lines += render_function(name, "q%d" % rng.randrange(10**6), snippets, rng)
            lines.append("")
        path = os.path.join(OUT_DIR, "mod_%04d.py" % file_idx)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        loc += len(lines) + 1
        file_idx += 1

    with open(LABELS, "w", encoding="utf-8") as fh:
        json.dump(planted, fh)
    return loc, file_idx


def main():
    regenerate = "--keep" not in sys.argv
    if regenerate or not os.path.isdir(OUT_DIR):
        t0 = time.perf_counter()
        loc, nfiles = generate()
        print("生成代码库: %d 个文件, 约 %d 行 (生成耗时 %.1fs)"
              % (nfiles, loc, time.perf_counter() - t0))
    else:
        loc = sum(1 for root, _, fs in os.walk(OUT_DIR) for f in fs
                  for _ in open(os.path.join(root, f), encoding="utf-8"))
        print("复用已有代码库, 约 %d 行" % loc)

    t0 = time.perf_counter()
    blocks, pairs = cd.detect([OUT_DIR], min_tokens=30, threshold=0.6)
    elapsed = time.perf_counter() - t0
    print("代码块: %d  克隆对: %d" % (len(blocks), len(pairs)))
    print("检测耗时: %.2f s  (%.0f 行/s)" % (elapsed, loc / elapsed))

    with open(LABELS, encoding="utf-8") as fh:
        planted = json.load(fh)
    detected = {tuple(sorted((os.path.basename(p.a.key).split("::")[-1],
                              os.path.basename(p.b.key).split("::")[-1])))
                for p in pairs}
    hit = sum(1 for a, b in planted if tuple(sorted((a, b))) in detected)
    print("种植克隆召回: %d/%d = %.1f%%" % (hit, len(planted), 100.0 * hit / len(planted)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
