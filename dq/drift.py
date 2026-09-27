"""分布漂移：基线快照的构建、加载与 PSI 计算。

关键约束：分箱边界在构建基线时冻结，之后任何新数据都只按
冻结边界落箱，判定标准（边界 + 基线分布）绝不因新数据改变。
"""
from __future__ import annotations

import bisect
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

OTHER_CATEGORY = "__OTHER__"


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _now_iso(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def build_baseline(
    values: Iterable[Any],
    field: str,
    bins: int = 10,
    kind: str = "auto",
    max_age_days: Optional[int] = None,
    top_k: int = 50,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """从参考数据构建基线快照（分箱边界在此冻结）。

    kind: "numeric" | "categorical" | "auto"（全部可转数值则按数值处理）。
    """
    materialized = [v for v in values if not _is_missing(v)]
    if kind == "auto":
        kind = "numeric" if all(_to_float(v) is not None for v in materialized) else "categorical"

    baseline: Dict[str, Any] = {
        "field": field,
        "kind": kind,
        "created_at": _now_iso(now),
        "max_age_days": max_age_days,
        "total": len(materialized),
    }

    if kind == "numeric":
        nums = sorted(_to_float(v) for v in materialized)  # type: ignore[arg-type]
        if not nums:
            raise ValueError(f"字段 {field} 没有可用数值，无法构建数值基线")
        lo, hi = nums[0], nums[-1]
        if hi > lo:
            width = (hi - lo) / bins
            edges = [lo + i * width for i in range(1, bins)]
        else:
            edges = []
        counts = [0] * (len(edges) + 1)
        for v in nums:
            counts[bisect.bisect_right(edges, v)] += 1
        baseline["bin_edges"] = edges
        baseline["bin_counts"] = counts
    else:
        freq: Dict[str, int] = {}
        for v in materialized:
            key = str(v)
            freq[key] = freq.get(key, 0) + 1
        top = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
        categories = [k for k, _ in top]
        counts = [c for _, c in top]
        counts.append(sum(freq.values()) - sum(counts))  # __OTHER__ 兜底箱
        baseline["categories"] = categories
        baseline["bin_counts"] = counts

    return baseline


def save_baseline(baseline: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(baseline, fh, ensure_ascii=False, indent=2)


def load_baseline(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def bin_index(baseline: Dict[str, Any], value: Any) -> Optional[int]:
    """按基线冻结的边界/类别给新数据落箱；缺失或不可比较返回 None。"""
    if _is_missing(value):
        return None
    if baseline["kind"] == "numeric":
        num = _to_float(value)
        if num is None:
            return None
        return bisect.bisect_right(baseline["bin_edges"], num)
    categories = baseline["categories"]
    key = str(value)
    try:
        return categories.index(key)
    except ValueError:
        return len(categories)  # __OTHER__


def bin_label(baseline: Dict[str, Any], index: int) -> str:
    """给出分箱的人类可读标签。"""
    if baseline["kind"] == "numeric":
        edges = baseline["bin_edges"]
        lo = "-inf" if index == 0 else f"{edges[index - 1]:.6g}"
        hi = "+inf" if index >= len(edges) else f"{edges[index]:.6g}"
        return f"[{lo}, {hi})"
    categories = baseline["categories"]
    return categories[index] if index < len(categories) else OTHER_CATEGORY


def psi(
    base_counts: List[int],
    cur_counts: List[int],
    eps: float = 1e-4,
) -> Tuple[float, List[Dict[str, float]]]:
    """计算 PSI（群体稳定性指数）及每个分箱的贡献。

    返回 (总 PSI, [{index, base_ratio, cur_ratio, contribution}, ...])。
    经验阈值：<0.1 稳定，0.1~0.25 轻度漂移，>=0.25 显著漂移。
    """
    base_total = sum(base_counts)
    cur_total = sum(cur_counts)
    if base_total == 0 or cur_total == 0:
        raise ValueError("PSI 计算要求基线与当前分布均非空")
    total = 0.0
    per_bin: List[Dict[str, float]] = []
    for i, (b, c) in enumerate(zip(base_counts, cur_counts)):
        bp = max(b / base_total, eps)
        cp = max(c / cur_total, eps)
        contribution = (cp - bp) * math.log(cp / bp)
        total += contribution
        per_bin.append(
            {
                "index": i,
                "base_ratio": bp,
                "cur_ratio": cp,
                "contribution": contribution,
            }
        )
    return total, per_bin


def baseline_age_days(baseline: Dict[str, Any], now: Optional[datetime] = None) -> float:
    created = datetime.fromisoformat(baseline["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    moment = now or datetime.now(timezone.utc)
    return (moment - created).total_seconds() / 86400.0


def is_stale(baseline: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    max_age = baseline.get("max_age_days")
    if max_age is None:
        return False
    return baseline_age_days(baseline, now) > max_age
