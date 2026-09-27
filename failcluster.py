"""failcluster: 回归失败归因聚类框架（仅标准库）。

核心思路：
1. 对每条失败记录做噪声归一化（随机序列号 / 内存地址 / 耗时 / 时间戳 / UUID 等），
   保证"同一根因、不同噪声"的样本归一化后完全一致。
2. 从归一化文本中抽取可比较特征：错误类型、堆栈关键帧（文件+函数，忽略行号）、
   断言差异（expected/actual 两侧分别归一化）。
3. 由特征生成签名（signature），签名相同即同组 —— O(n) 精确分组，
   每组给出代表样本与规模。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 1. 噪声归一化
# ---------------------------------------------------------------------------

_NOISE_RULES: list[tuple[re.Pattern[str], str]] = [
    # ISO / 常见时间戳（先于纯数字规则，避免被拆碎）
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"), "<TS>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b"), "<TS>"),
    # UUID
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    # 内存地址 / 十六进制数（0x1a2b3c、object at 0x7f...）
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<ADDR>"),
    # 耗时：123ms / 1.23s / 45 min / took 2.5 seconds / elapsed=0.03s
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|millisecond|milliseconds|s|sec|secs|second|seconds|min|minutes|h|hours)\b", re.IGNORECASE), "<DURATION>"),
    (re.compile(r"\b(?:took|elapsed|duration|cost|spent|latency)\s*[=:]?\s*\d+(?:\.\d+)?\b", re.IGNORECASE), "<DURATION>"),
    # 随机序列号 / 长数字 id（>=4 位，避免误伤错误码、小整数断言值）
    (re.compile(r"\b\d{4,}\b"), "<NUM>"),
    # 长十六进制串（无 0x 前缀的 hash / token，>=8 位）
    (re.compile(r"\b[0-9a-fA-F]{8,}\b"), "<HEX>"),
    # 临时文件 / 随机路径段
    (re.compile(r"/tmp/[\w.\-]+"), "/tmp/<TMP>"),
    # 端口号
    (re.compile(r"\bport\s+\d+\b", re.IGNORECASE), "port <NUM>"),
    # 行号（堆栈里的 line N）
    (re.compile(r", line \d+"), ", line <LN>"),
    (re.compile(r"\(\d+\)\s*$"), ""),  # 句尾 "(1)" 之类计数
]

_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """对失败文本做噪声归一化：随机序列号、内存地址、耗时数字等全部替换为占位符。"""
    out = text
    for pattern, repl in _NOISE_RULES:
        out = pattern.sub(repl, out)
    return _WS_RE.sub(" ", out).strip()


# ---------------------------------------------------------------------------
# 2. 特征抽取
# ---------------------------------------------------------------------------

_FRAME_RE = re.compile(
    r'^\s*File "(?P<file>[^"]+)", line \d+(?:, in (?P<func>\S+))?', re.MULTILINE
)
# Java/JS 风格: at com.foo.Bar.baz(Bar.java:42) / at func (file.js:10:5)
_AT_FRAME_RE = re.compile(
    r"^\s*at\s+(?P<func>[\w.$<>]+)\s*\((?P<file>[^():]+)(?::\d+){0,2}\)", re.MULTILINE
)
_ERROR_LINE_RE = re.compile(
    r"^(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Failure|Timeout|Abort|Panic|Fault))\b[:\s]*(?P<msg>.*)$",
    re.MULTILINE,
)
_ASSERT_PATTERNS: list[re.Pattern[str]] = [
    # Python: AssertionError: 'foo' != 'bar'  /  assert 1 == 2
    re.compile(r"AssertionError[:\s]*(?P<l>.+?)\s*(?:!=|==|is not|is)\s*(?P<r>.+)$"),
    # JUnit 风格: expected:<foo> but was:<bar>
    re.compile(r"expected\s*:\s*<(?P<l>.*?)>\s*but\s+was\s*:\s*<(?P<r>.*?)>", re.IGNORECASE),
    # 通用: Expected: foo, Actual: bar / expected foo but got bar
    re.compile(r"[Ee]xpected[:\s]+(?P<l>.+?)[,;]?\s+(?:but\s+(?:was|got)\s*[:\s]*|[Aa]ctual[:\s]+)(?P<r>.+)$"),
]

_TOP_FRAMES = 3  # 取最深（最后）的 N 帧作为关键帧


@dataclass(frozen=True)
class Features:
    """一条失败记录的可比较特征。"""

    error_type: str
    frames: tuple[str, ...]        # 关键帧，"file:func" 形式，忽略行号
    assertion: tuple[str, str] | None  # (归一化 expected, 归一化 actual)
    message: str                   # 归一化后的错误消息（兜底特征）


def _extract_frames(text: str) -> tuple[str, ...]:
    frames: list[str] = []
    for m in _FRAME_RE.finditer(text):
        fname = m.group("file").rsplit("/", 1)[-1]
        frames.append(f"{fname}:{m.group('func') or '?'}")
    for m in _AT_FRAME_RE.finditer(text):
        fname = m.group("file").rsplit("/", 1)[-1]
        frames.append(f"{fname}:{m.group('func')}")
    return tuple(frames[-_TOP_FRAMES:]) if frames else ()


def _extract_assertion(text: str) -> tuple[str, str] | None:
    for pat in _ASSERT_PATTERNS:
        m = pat.search(text)
        if m:
            return (normalize(m.group("l"))[:200], normalize(m.group("r"))[:200])
    return None


def extract_features(raw_text: str) -> Features:
    """从原始失败文本抽取特征。堆栈缺失时退化为 错误类型+归一化消息。"""
    error_type = "UnknownError"
    message = ""
    m = _ERROR_LINE_RE.search(raw_text)
    if m:
        error_type = m.group("type")
        message = normalize(m.group("msg"))
    else:
        # 无标准错误行：取最后一行非空文本作为消息
        lines = [ln.strip() for ln in raw_text.strip().splitlines() if ln.strip()]
        message = normalize(lines[-1]) if lines else ""
    return Features(
        error_type=error_type,
        frames=_extract_frames(raw_text),
        assertion=_extract_assertion(raw_text),
        message=message[:300],
    )


def signature_of(features: Features) -> str:
    """由特征生成稳定签名。特征一致 => 同组。"""
    parts = [
        features.error_type,
        "|".join(features.frames),
        "==".join(features.assertion) if features.assertion else "",
        features.message,
    ]
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 3. 聚类
# ---------------------------------------------------------------------------

@dataclass
class FailureRecord:
    """输入记录：id + 原始失败文本（traceback / 日志）。"""

    id: str
    text: str


@dataclass
class Cluster:
    signature: str
    representative: FailureRecord
    features: Features
    members: list[FailureRecord] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.members)


def cluster_failures(records: Iterable[FailureRecord]) -> list[Cluster]:
    """按签名聚类。返回按规模降序的簇列表，每簇含代表样本与规模。"""
    groups: dict[str, Cluster] = {}
    for rec in records:
        feats = extract_features(rec.text)
        sig = signature_of(feats)
        cluster = groups.get(sig)
        if cluster is None:
            cluster = Cluster(signature=sig, representative=rec, features=feats)
            groups[sig] = cluster
        cluster.members.append(rec)
    clusters = sorted(groups.values(), key=lambda c: (-c.size, c.signature))
    # 代表样本：选簇内归一化消息出现次数最多的那条（众数），更"典型"
    for cluster in clusters:
        counts: dict[str, FailureRecord] = {}
        freq: dict[str, int] = {}
        for rec in cluster.members:
            key = normalize(rec.text)
            freq[key] = freq.get(key, 0) + 1
            counts.setdefault(key, rec)
        best = max(freq, key=lambda k: (freq[k], -len(k)))
        cluster.representative = counts[best]
    return clusters


def load_records(path: str) -> list[FailureRecord]:
    """从 JSON 加载：[{"id": ..., "text": ...}, ...]，可选带 "label" 字段。"""
    with open(path, encoding="utf-8") as fh:
        data: list[dict[str, Any]] = json.load(fh)
    return [FailureRecord(id=str(item["id"]), text=item["text"]) for item in data]


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法: python3 failcluster.py <failures.json>", file=sys.stderr)
        return 2
    records = load_records(argv[1])
    clusters = cluster_failures(records)
    print(f"共 {len(records)} 条失败 -> {len(clusters)} 组\n")
    for idx, cluster in enumerate(clusters, 1):
        feats = cluster.features
        print(f"[组 {idx}] 规模={cluster.size}  签名={cluster.signature}")
        print(f"  错误类型 : {feats.error_type}")
        if feats.frames:
            print(f"  关键帧   : {' <- '.join(feats.frames)}")
        if feats.assertion:
            print(f"  断言差异 : expected={feats.assertion[0]!r} actual={feats.assertion[1]!r}")
        print(f"  代表样本 : {cluster.representative.id}")
        head = cluster.representative.text.strip().splitlines()
        print(f"             {head[-1][:120] if head else ''}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
