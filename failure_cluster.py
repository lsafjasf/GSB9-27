#!/usr/bin/env python3
"""failure_cluster.py — 失败归因聚类框架（仅 Python 标准库）

思路：
  1. 归一化：抹掉随机序列号、内存地址、耗时、时间戳等噪声；
  2. 特征抽取：错误类型 + 堆栈关键帧 + 断言差异行；
  3. 签名聚类：特征哈希相同的失败归为一组（O(n)，可上万条）；
  4. 评估：与人工标注对拍，输出 pairwise 准确率/漏并/错并。

用法：
    python failure_cluster.py cluster failures.json        # 聚类，输入为字符串数组
    python failure_cluster.py eval labeled_failures.json   # 对拍，输入为 [{"label":..,"message":..}]
    python failure_cluster.py bench 20000                  # 性能基准
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass

# ---------------------------------------------------------------- 噪声归一化

# 顺序敏感：先整体 token（request_id=xxx / UUID），再地址/时间/耗时，最后长数字。
_NOISE_RULES = [
    (re.compile(r'(?i)\b(?:request|trace|session|order|task|job|run|seq|serial|correlation)[_-]?id\s*[:=]\s*[\w.-]+'), '<RID>'),
    (re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b'), '<UUID>'),
    (re.compile(r'0x[0-9a-fA-F]+'), '<ADDR>'),                                   # 内存地址
    (re.compile(r'\b[0-9a-fA-F]{12,}\b'), '<HEX>'),                              # 长十六进制串
    (re.compile(r'\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b'), '<TS>'),
    (re.compile(r'\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b'), '<TIME>'),
    (re.compile(r'\b\d+(?:\.\d+)?\s*(?:ms|millis|milliseconds|s|sec|secs|seconds|minutes?|hours?)\b', re.IGNORECASE), '<DURATION>'),
    (re.compile(r'\b(?:pid|tid|port)\s*[:=]\s*\d+\b', re.IGNORECASE), '<PROC>'),
    (re.compile(r'\b\d{5,}\b'), '<SEQ>'),                                        # 随机序列号/长数字
    (re.compile(r'\bline\s+\d+\b'), 'line #'),                                   # 堆栈行号
    (re.compile(r'(?<=[:/(])\d+(?=\))'), '#'),                                   # Java 帧内行号 File.java:123)
]

_NOISY_SEGMENT = re.compile(r'\s*[(\[][^()\[\]]*<[^()\[\]]*[)\]]')
_WS = re.compile(r'\s+')


def normalize(text: str) -> str:
    """抹掉噪声，得到可比较的规范化文本。"""
    for pattern, repl in _NOISE_RULES:
        text = pattern.sub(repl, text)
    text = _NOISY_SEGMENT.sub('', text)  # 剔除只含噪声的括号段，如 (request_id=.., took ..)
    return _WS.sub(' ', text).strip()


# ---------------------------------------------------------------- 特征抽取

_PY_FRAME = re.compile(r'^\s*File "(?P<file>[^"]+)", line \d+, in (?P<func>\S+)')
_JVM_FRAME = re.compile(r'^\s*at\s+(?P<frame>[\w.$]+)\([^)]*\)')
_ERR_TYPE = re.compile(r'\b([A-Za-z_][\w.$]*(?:Error|Exception|Failure|Panic|Abort|error|failed))\b')
_ASSERT_LINE = re.compile(r'(?i)\b(assert|expected|actual|mismatch)\b|!=|but (was|got)\b')

MAX_KEY_FRAMES = 3


def _extract_error_type(lines):
    """取最后一个出现 Error/Exception 类 token 的行（兼容 Python 尾置与 Java 头置）。"""
    found = None
    for line in lines:
        m = _ERR_TYPE.search(line)
        if m:
            found = (m.group(1), line.strip())
    return found  # (类型, 所在行) 或 None


def _extract_frames(lines):
    """抽取关键帧（最深帧优先）。行号不参与。Python 栈底在末尾，JVM 栈顶在开头。"""
    py_frames, jvm_frames = [], []
    for line in lines:
        m = _PY_FRAME.match(line)
        if m:
            py_frames.append((m.group('file').rsplit('/', 1)[-1], m.group('func')))
            continue
        m = _JVM_FRAME.match(line)
        if m:
            jvm_frames.append(m.group('frame'))
    if jvm_frames:
        return list(jvm_frames[:MAX_KEY_FRAMES])
    py_frames.reverse()
    return py_frames[:MAX_KEY_FRAMES]


def _extract_assertion(lines):
    for line in lines:
        if _ASSERT_LINE.search(line):
            return normalize(line)[:200]
    return ''


def extract_features(text: str) -> dict:
    """从失败文本抽取可比较特征。"""
    lines = text.strip().splitlines() or ['']
    err = _extract_error_type(lines)
    if err:
        error_type, err_line = err
        message_head = normalize(err_line)[:200]
    else:
        error_type = ''
        message_head = normalize(lines[0].strip())[:200]
    assertion = _extract_assertion(lines)
    frames = _extract_frames(lines)
    return {
        'error_type': error_type,
        'key_frame': frames[0] if frames else None,
        'frames': frames,
        'assertion': assertion,
        'message_head': assertion or message_head,
    }


_SIGNATURE_KEYS = ('error_type', 'key_frame', 'message_head')


def signature_of(text: str) -> str:
    feats = extract_features(text)
    payload = json.dumps({k: feats[k] for k in _SIGNATURE_KEYS}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]


# ---------------------------------------------------------------- 聚类

@dataclass
class Cluster:
    signature: str
    members: list          # 原始下标
    representative: str    # 代表样本（组内最常见规范化形态的首条原文）
    features: dict

    @property
    def size(self) -> int:
        return len(self.members)


def cluster_failures(failures: list) -> list:
    """输入失败文本列表，输出按规模降序的 Cluster 列表。"""
    buckets = defaultdict(list)
    feats_by_sig = {}
    for idx, text in enumerate(failures):
        feats = extract_features(text)
        payload = json.dumps({k: feats[k] for k in _SIGNATURE_KEYS}, sort_keys=True, ensure_ascii=False)
        sig = hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]
        buckets[sig].append(idx)
        feats_by_sig[sig] = feats

    clusters = []
    for sig, members in buckets.items():
        # 代表样本：组内规范化报文出现次数最多的形态
        forms = Counter(normalize(failures[i]) for i in members)
        best_form = forms.most_common(1)[0][0]
        rep = next(failures[i] for i in members if normalize(failures[i]) == best_form)
        clusters.append(Cluster(sig, members, rep, feats_by_sig[sig]))
    clusters.sort(key=lambda c: -c.size)
    return clusters


def report(clusters: list, total: int) -> str:
    out = [f'共 {total} 条失败，归并为 {len(clusters)} 组：']
    for rank, c in enumerate(clusters, 1):
        first_line = c.representative.strip().splitlines()[0][:100]
        out.append(f'  [{rank}] 组 {c.signature}  规模={c.size}  类型={c.features["error_type"] or "?"}')
        out.append(f'      代表: {first_line}')
    return '\n'.join(out)


# ---------------------------------------------------------------- 对拍评估

def _comb2(n):
    return n * (n - 1) // 2


def evaluate(clusters: list, labels: list) -> dict:
    """与人工标注对拍。

    - pairwise precision/recall/F1：把"同组"视为预测同一根因；
    - 漏并：标注相同却被拆到不同组的样本对；
    - 错并：标注不同却被并进同一组的样本对。
    """
    label_of = {i: labels[i] for c in clusters for i in c.members}
    cluster_of = {i: c.signature for c in clusters for i in c.members}

    by_label = Counter(label_of.values())
    join = Counter((label_of[i], cluster_of[i]) for i in label_of)

    same_label_pairs = sum(_comb2(n) for n in by_label.values())
    merged_pairs = sum(_comb2(c.size) for c in clusters)
    correct_pairs = sum(_comb2(n) for n in join.values())

    missed = same_label_pairs - correct_pairs      # 漏并
    wrong = merged_pairs - correct_pairs           # 错并
    precision = correct_pairs / merged_pairs if merged_pairs else 1.0
    recall = correct_pairs / same_label_pairs if same_label_pairs else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 1.0
    return {
        '样本数': len(label_of),
        '标注根因数': len(by_label),
        '聚类组数': len(clusters),
        '同标注样本对': same_label_pairs,
        '正确归并对': correct_pairs,
        '漏并对': missed,
        '错并对': wrong,
        'pairwise_precision': round(precision, 4),
        'pairwise_recall': round(recall, 4),
        'pairwise_f1': round(f1, 4),
        '漏并率': round(missed / same_label_pairs, 4) if same_label_pairs else 0.0,
        '错并率': round(wrong / merged_pairs, 4) if merged_pairs else 0.0,
    }


# ---------------------------------------------------------------- 基准数据

_BENCH_TEMPLATES = [
    'Traceback (most recent call last):\n  File "/app/tests/test_order.py", line {n}, in test_order\n    run()\n  File "/app/svc/order.py", line {n}, in run\n    db.execute(q)\nKeyError: \'price\' (request_id=req-{seq}, took {dur}ms)',
    'java.lang.NullPointerException: Cannot invoke "String.length()" because "user" is null\n\tat com.shop.UserService.get(UserService.java:{n})\n\tat com.shop.Ctrl.handle(Ctrl.java:{n})\n\tat com.shop.Main.main(Main.java:{n})',
    'AssertionError: expected status 200 but got 500 for POST /orders (trace_id={uuid}, elapsed {dur}s)',
    'TimeoutError: query exceeded {dur}ms at 0x{hex} (pid={n})',
    'ConnectionRefusedError: [Errno 111] connect to redis://10.0.0.9:6379 failed after {dur}s (attempt {n})',
    'AssertionError: lists differ: [1, 2, {m}] != [1, 2, {k}] (case #{seq})',
    'FileNotFoundError: [Errno 2] No such file or directory: \'/etc/app/conf-{seq}.yaml\'',
    'PermissionError: [Errno 13] Permission denied: \'/var/log/app/{seq}.log\' at 0x{hex}',
]


def _gen_bench(n, seed=7):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        t = rng.choice(_BENCH_TEMPLATES)
        out.append(t.format(n=rng.randint(1, 999), m=rng.randint(1, 9), k=rng.randint(1, 9),
                            seq=rng.randint(10000, 99999999), dur=rng.randint(1, 90000),
                            hex='%x' % rng.getrandbits(48),
                            uuid='%08x-%04x-%04x-%04x-%012x' % tuple(rng.getrandbits(4 * x) for x in (8, 4, 4, 4, 12))))
    return out


# ---------------------------------------------------------------- CLI

def _cmd_cluster(path):
    with open(path, encoding='utf-8') as f:
        failures = json.load(f)
    clusters = cluster_failures(failures)
    print(report(clusters, len(failures)))


def _cmd_eval(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    messages = [d['message'] for d in data]
    labels = [d['label'] for d in data]
    clusters = cluster_failures(messages)
    print(report(clusters, len(messages)))
    print('\n对拍结果:')
    for k, v in evaluate(clusters, labels).items():
        print(f'  {k}: {v}')


def _cmd_bench(n):
    failures = _gen_bench(n)
    t0 = time.perf_counter()
    clusters = cluster_failures(failures)
    dt = time.perf_counter() - t0
    print(f'{n} 条失败聚类耗时 {dt:.3f}s（{n / dt:.0f} 条/s），归并为 {len(clusters)} 组')
    print('Top3 组规模:', [c.size for c in clusters[:3]])


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd, arg = sys.argv[1], sys.argv[2]
    if cmd == 'cluster':
        _cmd_cluster(arg)
    elif cmd == 'eval':
        _cmd_eval(arg)
    elif cmd == 'bench':
        _cmd_bench(int(arg))
    else:
        print(__doc__)
        sys.exit(1)
