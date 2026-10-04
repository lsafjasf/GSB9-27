"""指标标签基数控制库（仅标准库）。

三件套：
- CardinalityTracker: 按时间窗口观察标签取值，依据"新值增长比 / 绝对基数 /
  取值形态"识别高基数标签，并给出基数统计。
- LabelNormalizer:   按识别结果归并（top-K + other）或丢弃（ID 类）标签，
  记录归并映射与丢弃计数。
- MetricStore:       极简时序点存储，用于对比归并前后的聚合查询结果。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

OTHER = "__other__"

# 典型"请求号/用户标识"形态：UUID、纯长十六进制、纯长数字、带连字符的长随机串
_ID_PATTERNS = [
    re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
               r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),          # UUID
    re.compile(r"^[0-9a-fA-F]{16,}$"),                       # 长十六进制
    re.compile(r"^[0-9]{8,}$"),                              # 长数字（时间戳/自增ID）
    re.compile(r"^(?=.*[0-9])(?=.*[A-Za-z])[A-Za-z0-9_-]{12,}$"),  # 混合随机串
]


def looks_like_id(value: str) -> bool:
    """判断单个取值是否像请求号/用户标识。"""
    return any(p.match(value) for p in _ID_PATTERNS)


@dataclass
class LabelStats:
    """单个标签键的基数统计与识别结论。"""
    key: str
    sample_count: int = 0
    distinct: int = 0
    window_new_values: List[int] = field(default_factory=list)  # 每窗口新增取值数
    window_samples: List[int] = field(default_factory=list)     # 每窗口样本数
    id_like_hits: int = 0
    value_counts: Counter = field(default_factory=Counter)
    high_cardinality: bool = False
    kind: str = "stable"          # stable / growing / id_like
    reasons: List[str] = field(default_factory=list)

    @property
    def id_like_ratio(self) -> float:
        return self.id_like_hits / self.sample_count if self.sample_count else 0.0

    @property
    def growth_ratio(self) -> float:
        """第二窗口起，新值数 / 样本数 的均值；越高说明取值几乎不重复。"""
        if len(self.window_samples) < 2:
            return 0.0
        new_vals = sum(self.window_new_values[1:])
        samples = sum(self.window_samples[1:])
        return new_vals / samples if samples else 0.0

    def to_report(self) -> dict:
        return {
            "key": self.key,
            "sample_count": self.sample_count,
            "distinct": self.distinct,
            "windows": len(self.window_samples),
            "growth_ratio": round(self.growth_ratio, 4),
            "id_like_ratio": round(self.id_like_ratio, 4),
            "high_cardinality": self.high_cardinality,
            "kind": self.kind,
            "reasons": list(self.reasons),
            "top_values": self.value_counts.most_common(5),
        }


class CardinalityTracker:
    """按窗口采集标签取值并识别高基数标签。

    判定依据（满足任一即标记 high_cardinality）：
      1. 绝对基数 distinct >= max_cardinality；
      2. 跨窗口增长比 growth_ratio >= growth_ratio_threshold
         （后期窗口里出现过的新值占比高，即取值持续增长）；
      3. id_like_ratio >= id_ratio_threshold（取值普遍像请求号），
         此时 kind=id_like；满足 2 但不满足 3 时 kind=growing。
    """

    def __init__(self,
                 window_size: int = 100,
                 max_cardinality: int = 100,
                 growth_ratio_threshold: float = 0.5,
                 id_ratio_threshold: float = 0.8,
                 min_distinct: int = 8) -> None:
        self.window_size = window_size
        self.max_cardinality = max_cardinality
        self.growth_ratio_threshold = growth_ratio_threshold
        self.id_ratio_threshold = id_ratio_threshold
        self.min_distinct = min_distinct
        self._stats: Dict[str, LabelStats] = {}
        self._seen: Dict[str, set] = defaultdict(set)

    def observe_many(self, label_sets: Iterable[Dict[str, str]]) -> None:
        for labels in label_sets:
            self.observe(labels)

    def observe(self, labels: Dict[str, str]) -> None:
        window_index = self._stats  # 占位，保持可读性
        for key, raw in labels.items():
            value = "" if raw is None else str(raw)
            st = self._stats.get(key)
            if st is None:
                st = LabelStats(key=key)
                self._stats[key] = st
                self._seen[key] = set()

            # 当前窗口的样本计数；窗口按该标签的样本序号切分
            w = st.sample_count // self.window_size
            if w == len(st.window_samples):
                st.window_samples.append(0)
                st.window_new_values.append(0)

            st.sample_count += 1
            st.window_samples[w] += 1
            st.value_counts[value] += 1
            if looks_like_id(value):
                st.id_like_hits += 1
            if value not in self._seen[key]:
                self._seen[key].add(value)
                st.distinct += 1
                st.window_new_values[w] += 1

    def classify(self) -> Dict[str, LabelStats]:
        for st in self._stats.values():
            st.high_cardinality = False
            st.kind = "stable"
            st.reasons = []

            if st.distinct >= self.max_cardinality:
                st.high_cardinality = True
                st.reasons.append(
                    f"distinct={st.distinct} >= max_cardinality={self.max_cardinality}")
            if (len(st.window_samples) >= 2
                    and st.distinct >= self.min_distinct
                    and st.growth_ratio >= self.growth_ratio_threshold):
                st.high_cardinality = True
                st.reasons.append(
                    f"growth_ratio={st.growth_ratio:.2f} "
                    f">= {self.growth_ratio_threshold}")
            if st.id_like_ratio >= self.id_ratio_threshold and st.sample_count >= 4:
                st.high_cardinality = True
                st.kind = "id_like"
                st.reasons.append(
                    f"id_like_ratio={st.id_like_ratio:.2f} "
                    f">= {self.id_ratio_threshold}")
            elif st.high_cardinality:
                st.kind = "growing"
        return self._stats

    def report(self) -> List[dict]:
        return [s.to_report() for s in self.classify().values()]


class LabelNormalizer:
    """按规则归并/丢弃标签取值。

    - id_like 标签：整个标签丢弃（取值无维度意义），计入 dropped；
    - growing 标签：保留出现频次 top_k 的取值，其余归并到 __other__；
    - stable 标签：原样保留。
    归并映射按 (key, value) -> bucket 记录，上限 mapping_cap 条防自身爆炸。
    """

    def __init__(self,
                 stats: Dict[str, LabelStats],
                 top_k: int = 10,
                 mapping_cap: int = 1000) -> None:
        self.top_k = top_k
        self.mapping_cap = mapping_cap
        self.dropped_count: Counter = Counter()      # 被整标签丢弃的样本数
        self.merged_sample_count: Counter = Counter() # 被并入 other 的样本数
        self.kept_sample_count: Counter = Counter()
        self.mapping_overflow = 0
        self.mapping: Dict[Tuple[str, str], str] = {}
        self._keep: Dict[str, set] = {}
        self._drop: set = set()
        self._configure(stats)

    def _configure(self, stats: Dict[str, LabelStats]) -> None:
        for key, st in stats.items():
            if st.kind == "id_like":
                self._drop.add(key)
            elif st.high_cardinality:
                top = {v for v, _ in st.value_counts.most_common(self.top_k)}
                self._keep[key] = top
            # stable: 无需记录

    def normalize(self, labels: Dict[str, str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for key, raw in labels.items():
            value = "" if raw is None else str(raw)
            if key in self._drop:
                self.dropped_count[key] += 1
                continue
            keep = self._keep.get(key)
            if keep is not None and value not in keep:
                out[key] = OTHER
                self.merged_sample_count[key] += 1
                pair = (key, value)
                if pair not in self.mapping:
                    if len(self.mapping) < self.mapping_cap:
                        self.mapping[pair] = OTHER
                    else:
                        self.mapping_overflow += 1
            else:
                out[key] = value
                self.kept_sample_count[key] += 1
        return out

    def mapping_report(self) -> Dict[str, List[Tuple[str, str]]]:
        """按标签给出 原取值 -> 归并桶 的映射。"""
        rep: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        for (key, value), bucket in sorted(self.mapping.items()):
            rep[key].append((value, bucket))
        return dict(rep)

    def to_report(self) -> dict:
        return {
            "dropped_labels": sorted(self._drop),
            "dropped_sample_count": dict(self.dropped_count),
            "merged_to_other": {
                k: {
                    "distinct_values": len({v for (kk, v) in self.mapping if kk == k})
                                        + self.mapping_overflow,
                    "samples": self.merged_sample_count[k],
                }
                for k in self.merged_sample_count
            },
            "kept_sample_count": dict(self.kept_sample_count),
            "mapping_cap": self.mapping_cap,
            "mapping_overflow": self.mapping_overflow,
        }


@dataclass
class _Point:
    value: float
    count: int = 1


class MetricStore:
    """按 指标名+标签组合 聚合数值，支持分组查询，用于前后对比。"""

    def __init__(self) -> None:
        # (metric, sorted label tuple) -> [sum, count]
        self._series: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], List[float]] = {}

    def record(self, metric: str, labels: Dict[str, str], value: float = 1.0) -> None:
        key = (metric, tuple(sorted(labels.items())))
        agg = self._series.get(key)
        if agg is None:
            agg = [0.0, 0]
            self._series[key] = agg
        agg[0] += value
        agg[1] += 1

    def series_count(self, metric: str) -> int:
        return sum(1 for (m, _) in self._series if m == metric)

    def query(self,
              metric: str,
              group_by: Sequence[str] = (),
              where: Optional[Dict[str, str]] = None) -> List[dict]:
        """按 group_by 标签聚合求和；where 为等值过滤。

        返回 [{"labels": {k: v}, "sum": .., "count": ..}, ...]，按标签排序。
        样本缺失某分组标签时以空串参与分组（丢弃标签同样落到空串桶）。
        """
        result: Dict[Tuple[str, ...], List[float]] = defaultdict(lambda: [0.0, 0])
        for (m, label_tuples), agg in self._series.items():
            if m != metric:
                continue
            labels = dict(label_tuples)
            if where and any(labels.get(k) != v for k, v in where.items()):
                continue
            gk = tuple(labels.get(k, "") for k in group_by)
            result[gk][0] += agg[0]
            result[gk][1] += agg[1]
        rows = []
        for gk, (total, cnt) in sorted(result.items()):
            rows.append({
                "labels": dict(zip(group_by, gk)),
                "sum": round(total, 6),
                "count": cnt,
            })
        return rows

    def totals(self, metric: str) -> Tuple[float, int]:
        total, cnt = 0.0, 0
        for (m, _), agg in self._series.items():
            if m == metric:
                total += agg[0]
                cnt += agg[1]
        return round(total, 6), cnt
