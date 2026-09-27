#!/usr/bin/env python3
"""clone_detector.py — 语义级代码克隆检测框架（仅 Python 标准库）。

检测流程:
  1. 词法归一化: 用 tokenize 切词, 标识符 -> <ID>, 数字 -> <NUM>,
     字符串 -> <STR>, 丢弃空白/注释/缩进。仅重命名 (Type-2) 的代码
     归一化后 token 流完全一致。
  2. 特征提取: 对每个代码块 (函数/类) 提取两类 shingle 特征 —
     行内 k-gram (对语句换序不敏感) + 块级 k-gram (保留整体结构)。
  3. 候选生成: 小块全量两两比对; 大块用 MinHash + LSH 分桶,
     避免 O(n^2)。
  4. 精确比对: 对候选对计算 Jaccard 相似度, 超过阈值即为克隆对。

可识别: 完全重复 (Type-1)、重命名 (Type-2)、语句换序、部分重叠。
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import keyword
import os
import random
import sys
import time
import tokenize
import zlib
from dataclasses import dataclass

SKIP_TYPES = frozenset(
    t
    for t in (
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
        getattr(tokenize, "ENCODING", None),
    )
    if t is not None
)

MASK32 = (1 << 32) - 1


# ---------------------------------------------------------------- 词法归一化

def normalize_source(source):
    """源码 -> [(行号, [归一化 token, ...]), ...]，按行分组。"""
    by_line = {}
    reader = io.StringIO(source).readline
    try:
        for tok in tokenize.generate_tokens(reader):
            if tok.type in SKIP_TYPES:
                continue
            value = tok.string
            if tok.type == tokenize.NAME:
                if not keyword.iskeyword(value):
                    value = "<ID>"
            elif tok.type == tokenize.NUMBER:
                value = "<NUM>"
            elif tok.type == tokenize.STRING:
                value = "<STR>"
            by_line.setdefault(tok.start[0], []).append(value)
    except tokenize.TokenError:
        pass
    return sorted(by_line.items())


# ---------------------------------------------------------------- 代码块

@dataclass
class Block:
    file: str
    name: str
    start_line: int
    end_line: int
    lines: list  # list[list[str]] 每行的归一化 token

    @property
    def key(self):
        return "%s::%s" % (self.file, self.name)

    @property
    def num_lines(self):
        return self.end_line - self.start_line + 1

    @property
    def num_tokens(self):
        return sum(len(line) for line in self.lines)

    @property
    def tokens(self):
        return [tok for line in self.lines for tok in line]


def extract_blocks(path, min_tokens):
    """用 AST 切出函数/类级别的代码块，过滤过小的块（误报主来源）。"""
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    line_tokens = dict(normalize_source(source))
    blocks = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            lines = [
                line_tokens[ln]
                for ln in range(node.lineno, node.end_lineno + 1)
                if ln in line_tokens
            ]
            if sum(len(l) for l in lines) >= min_tokens:
                blocks.append(Block(path, node.name, node.lineno, node.end_lineno, lines))
    return blocks


# ---------------------------------------------------------------- 特征

def _hash_seq(seq):
    return zlib.crc32("\x00".join(seq).encode("utf-8"))


def block_features(block, line_k=4, block_k=5):
    """行内 shingle (换序鲁棒) ∪ 块级 shingle (结构信息) 的哈希集合。"""
    feats = set()
    flat = []
    for line in block.lines:
        flat.extend(line)
        if len(line) >= line_k:
            for i in range(len(line) - line_k + 1):
                feats.add(_hash_seq(line[i : i + line_k]))
        elif line:
            feats.add(_hash_seq(line))
    if len(flat) >= block_k:
        for i in range(len(flat) - block_k + 1):
            feats.add(_hash_seq(flat[i : i + block_k]))
    elif flat:
        feats.add(_hash_seq(flat))
    return feats


# ---------------------------------------------------------------- MinHash + LSH

def _minhash_params(num_perm, seed=1234567):
    rng = random.Random(seed)
    return [(rng.randrange(1, MASK32, 2), rng.randrange(0, MASK32)) for _ in range(num_perm)]


def minhash_sig(feats, params):
    sig = []
    for a, b in params:
        best = MASK32
        for h in feats:
            v = (a * h + b) & MASK32
            if v < best:
                best = v
        sig.append(best)
    return sig


# ---------------------------------------------------------------- 检测结果

@dataclass
class ClonePair:
    a: Block
    b: Block
    similarity: float

    def to_dict(self):
        return {
            "a": {"file": self.a.file, "name": self.a.name,
                  "lines": [self.a.start_line, self.a.end_line]},
            "b": {"file": self.b.file, "name": self.b.name,
                  "lines": [self.b.start_line, self.b.end_line]},
            "lines": [self.a.num_lines, self.b.num_lines],
            "tokens": [self.a.num_tokens, self.b.num_tokens],
            "similarity": round(self.similarity, 4),
        }


def _contained(b1, b2):
    """同一文件内行范围重叠 (嵌套函数) 的对不算克隆。"""
    return (
        b1.file == b2.file
        and b1.start_line <= b2.end_line
        and b2.start_line <= b1.end_line
    )


def detect(paths, min_tokens=30, threshold=0.6, line_k=4, block_k=5,
           num_perm=48, band_rows=3, exact_upto=400):
    """主入口: 返回 (blocks, clone_pairs)，按规模 (较小块 token 数) 降序。"""
    files = list(iter_python_files(paths))
    blocks = []
    for path in files:
        blocks.extend(extract_blocks(path, min_tokens))
    feats = [block_features(b, line_k, block_k) for b in blocks]
    n = len(blocks)

    candidates = set()
    if n <= exact_upto:
        candidates = {(i, j) for i in range(n) for j in range(i + 1, n)}
    else:
        params = _minhash_params(num_perm)
        sigs = [minhash_sig(f, params) for f in feats]
        buckets = {}
        for idx, sig in enumerate(sigs):
            for band in range(0, num_perm, band_rows):
                key = (band, tuple(sig[band : band + band_rows]))
                buckets.setdefault(key, []).append(idx)
        for members in buckets.values():
            for x in range(len(members)):
                for y in range(x + 1, len(members)):
                    candidates.add((members[x], members[y]))

    pairs = []
    for i, j in candidates:
        if _contained(blocks[i], blocks[j]):
            continue
        fa, fb = feats[i], feats[j]
        inter = len(fa & fb)
        if not inter:
            continue
        sim = inter / (len(fa) + len(fb) - inter)
        if sim >= threshold:
            pairs.append(ClonePair(blocks[i], blocks[j], sim))
    pairs.sort(key=lambda p: (min(p.a.num_tokens, p.b.num_tokens), p.similarity),
               reverse=True)
    return blocks, pairs


def iter_python_files(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for name in sorted(files):
                    if name.endswith(".py"):
                        yield os.path.join(root, name)
        elif p.endswith(".py"):
            yield p


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="语义级代码克隆检测 (仅标准库)")
    ap.add_argument("paths", nargs="+", help="待检测的 .py 文件或目录")
    ap.add_argument("--min-tokens", type=int, default=30,
                    help="代码块最小 token 数, 低于此值不参与比对 (默认 30)")
    ap.add_argument("--threshold", type=float, default=0.6,
                    help="Jaccard 相似度阈值 (默认 0.6)")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="将结果写入 JSON 文件")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    blocks, pairs = detect(args.paths, min_tokens=args.min_tokens,
                           threshold=args.threshold)
    elapsed = time.perf_counter() - t0

    print("扫描代码块: %d  克隆对: %d  耗时: %.2fs" % (len(blocks), len(pairs), elapsed))
    print("%-8s %-11s %-13s %s" % ("相似度", "行数 A/B", "Token A/B", "位置"))
    for p in pairs:
        print("%-8.3f %-11s %-13s %s::%s(L%d-%d)  <->  %s::%s(L%d-%d)" % (
            p.similarity,
            "%d/%d" % (p.a.num_lines, p.b.num_lines),
            "%d/%d" % (p.a.num_tokens, p.b.num_tokens),
            p.a.file, p.a.name, p.a.start_line, p.a.end_line,
            p.b.file, p.b.name, p.b.start_line, p.b.end_line,
        ))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump([p.to_dict() for p in pairs], fh, ensure_ascii=False, indent=2)
        print("结果已写入 %s" % args.json_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
