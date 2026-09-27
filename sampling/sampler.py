"""修复后的分层采样器（仅标准库）。

策略：
- 错误记录、超阈值记录：全量保留（采样率 1.0），保证故障现场不丢。
- 正常记录：按比例 p 采样，决策基于 sha256(seed, record_id)，
  对给定种子与输入序列完全确定、可复现，且与输入顺序无关。
- 正常记录的采样是无偏的：保留数 / p 即为总量的无偏估计。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

CATEGORY_ERROR = "error"
CATEGORY_OVER_THRESHOLD = "over_threshold"
CATEGORY_NORMAL = "normal"

# 全量保留的类别
ALWAYS_KEEP = frozenset({CATEGORY_ERROR, CATEGORY_OVER_THRESHOLD})


@dataclass(frozen=True)
class Record:
    record_id: str
    category: str
    payload: str = ""


def classify(status: int, latency_ms: float, threshold_ms: float) -> str:
    """根据响应状态与延迟给记录分类。"""
    if status >= 500:
        return CATEGORY_ERROR
    if latency_ms > threshold_ms:
        return CATEGORY_OVER_THRESHOLD
    return CATEGORY_NORMAL


def _uniform01(seed: str, record_id: str) -> float:
    """把 (seed, record_id) 确定性地映射到 [0, 1) 的均匀值。"""
    digest = hashlib.sha256(f"{seed}:{record_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


@dataclass
class CategoryStats:
    total: int = 0
    kept: int = 0
    dropped: int = 0

    @property
    def effective_rate(self) -> float:
        return self.kept / self.total if self.total else 1.0


@dataclass
class SampleResult:
    kept_records: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)  # category -> CategoryStats
    normal_rate: float = 0.0

    def normal_total_estimate(self):
        """正常记录总量的无偏估计；p=0 时不可估计，返回 None。"""
        normal = self.stats.get(CATEGORY_NORMAL)
        if normal is None or self.normal_rate <= 0.0:
            return None
        return normal.kept / self.normal_rate

    def normal_estimate_error(self):
        """估计值相对真实总量的相对误差；不可估计时返回 None。"""
        estimate = self.normal_total_estimate()
        normal = self.stats.get(CATEGORY_NORMAL)
        if estimate is None or normal is None or normal.total == 0:
            return None
        return (estimate - normal.total) / normal.total


class Sampler:
    """分层采样器：错误/超阈值全保留，正常记录按 normal_rate 采样。"""

    def __init__(self, seed: str, normal_rate: float):
        if not 0.0 <= normal_rate <= 1.0:
            raise ValueError(f"normal_rate 必须在 [0, 1] 内，收到 {normal_rate}")
        self._seed = str(seed)
        self._normal_rate = normal_rate

    @property
    def normal_rate(self) -> float:
        return self._normal_rate

    def keep(self, record: Record) -> bool:
        if record.category in ALWAYS_KEEP:
            return True
        return _uniform01(self._seed, record.record_id) < self._normal_rate

    def sample(self, records) -> SampleResult:
        result = SampleResult(normal_rate=self._normal_rate)
        for record in records:
            cat_stats = result.stats.setdefault(record.category, CategoryStats())
            cat_stats.total += 1
            if self.keep(record):
                cat_stats.kept += 1
                result.kept_records.append(record)
            else:
                cat_stats.dropped += 1
        return result
