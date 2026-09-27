"""确定性、无偏的监控记录采样器。

规则
----
* error（错误记录）与 over_threshold（超阈值记录）始终全量保留；
* normal（正常记录）按给定比例做独立伯努利采样；
* 采样决策只依赖 (seed, 记录在输入序列中的顺序)，同种子同序列结果完全一致。

无偏估计（Horvitz-Thompson / 逆概率加权）
----------------------------------------
每条被保留的 normal 记录在总体中的包含概率为 p，代表 1/p 条记录，因此：

    总数估计   N_hat = n_kept / p
    指标和估计 S_hat = sum(latency_kept) / p

两者均为总体真值的无偏估计；error / over_threshold 的包含概率为 1，权重为 1。
p == 0 时样本为空、逆概率无定义，估计返回 None（丢弃计数与采样率仍准确输出）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence

CATEGORY_ERROR = "error"
CATEGORY_OVER_THRESHOLD = "over_threshold"
CATEGORY_NORMAL = "normal"
CATEGORIES = (CATEGORY_ERROR, CATEGORY_OVER_THRESHOLD, CATEGORY_NORMAL)
FORCED_FULL_RATE = frozenset((CATEGORY_ERROR, CATEGORY_OVER_THRESHOLD))


@dataclass(frozen=True)
class Record:
    record_id: int
    latency_ms: float = 0.0
    is_error: bool = False


@dataclass
class CategoryStat:
    seen: int = 0
    kept: int = 0
    configured_rate: float = 1.0
    sampled_latency_sum: float = 0.0
    total_latency_sum: float = 0.0

    @property
    def dropped(self) -> int:
        return self.seen - self.kept

    @property
    def realized_rate(self) -> float:
        return (self.kept / self.seen) if self.seen else 1.0


@dataclass
class SamplingResult:
    kept: List[Record]
    dropped: List[Record]
    stats: Dict[str, CategoryStat]
    seed: object

    def sample_rates(self) -> Dict[str, float]:
        return {c: self.stats[c].configured_rate for c in CATEGORIES}

    def realized_rates(self) -> Dict[str, float]:
        return {c: self.stats[c].realized_rate for c in CATEGORIES}

    def drop_counts(self) -> Dict[str, int]:
        return {c: self.stats[c].dropped for c in CATEGORIES}

    def kept_id_set(self) -> frozenset:
        return frozenset(r.record_id for r in self.kept)

    def _categories(self, category: Optional[str]) -> Sequence[str]:
        if category is not None:
            if category not in self.stats:
                raise KeyError(f"unknown category: {category!r}")
            return (category,)
        return CATEGORIES

    def estimated_count(self, category: Optional[str] = None) -> Optional[float]:
        total = 0.0
        for cat in self._categories(category):
            stat = self.stats[cat]
            if stat.seen == 0:
                continue
            if stat.configured_rate <= 0.0:
                return None
            total += stat.kept / stat.configured_rate
        return total

    def estimated_latency_sum(self, category: Optional[str] = None) -> Optional[float]:
        total = 0.0
        for cat in self._categories(category):
            stat = self.stats[cat]
            if stat.seen == 0:
                continue
            if stat.configured_rate <= 0.0:
                return None
            total += stat.sampled_latency_sum / stat.configured_rate
        return total

    def report_lines(self) -> List[str]:
        lines = [
            f"seed={self.seed!r}",
            f"{'category':<16}{'rate':>8}{'realized':>10}{'seen':>8}{'kept':>8}{'dropped':>9}",
        ]
        for cat in CATEGORIES:
            stat = self.stats[cat]
            lines.append(
                f"{cat:<16}{stat.configured_rate:>7.1%}{stat.realized_rate:>9.1%}"
                f"{stat.seen:>8}{stat.kept:>8}{stat.dropped:>9}"
            )
        return lines


class Sampler:
    """按类别采样：错误/超阈值全量保留，正常记录按比例确定性采样。"""

    def __init__(
        self,
        normal_rate: float = 0.1,
        *,
        threshold_ms: float = 1000.0,
        seed: object = 0,
        classify: Optional[Callable[[Record], str]] = None,
    ) -> None:
        if not 0.0 <= float(normal_rate) <= 1.0:
            raise ValueError(f"normal_rate must be within [0, 1], got {normal_rate!r}")
        self.normal_rate = float(normal_rate)
        self.threshold_ms = float(threshold_ms)
        self.seed = seed
        self._classify = classify

    def classify(self, record: Record) -> str:
        if self._classify is not None:
            return self._classify(record)
        if record.is_error:
            return CATEGORY_ERROR
        if record.latency_ms > self.threshold_ms:
            return CATEGORY_OVER_THRESHOLD
        return CATEGORY_NORMAL

    def sample(self, records: Iterable[Record], *, seed: object = ...) -> SamplingResult:
        if seed is ...:
            seed = self.seed
        rng = random.Random(seed)
        stats = {
            cat: CategoryStat(
                configured_rate=1.0 if cat in FORCED_FULL_RATE else self.normal_rate
            )
            for cat in CATEGORIES
        }
        kept: List[Record] = []
        dropped: List[Record] = []

        for record in records:
            cat = self.classify(record)
            if cat not in stats:
                raise KeyError(f"classifier returned unknown category: {cat!r}")
            stat = stats[cat]
            stat.seen += 1
            stat.total_latency_sum += record.latency_ms

            if cat in FORCED_FULL_RATE:
                keep = True
            elif self.normal_rate <= 0.0:
                keep = False
            elif self.normal_rate >= 1.0:
                keep = True
            else:
                keep = rng.random() < self.normal_rate

            if keep:
                stat.kept += 1
                stat.sampled_latency_sum += record.latency_ms
                kept.append(record)
            else:
                dropped.append(record)

        return SamplingResult(kept=kept, dropped=dropped, stats=stats, seed=seed)
