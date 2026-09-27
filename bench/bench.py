"""性能基准：单条到百万条数据的检查耗时（纯标准库，单机单进程）。

用法：python -m dq.cli bench  或  python bench/bench.py
"""
from __future__ import annotations

import json
import os
import random
import tempfile
import time
from typing import Any, Dict, Iterator, List

from dq.drift import build_baseline, save_baseline
from dq.engine import Engine

CATEGORIES = ["app", "web", "api", "batch"]


def gen_rows(n: int, bad_ratio: float = 0.01, seed: int = 42) -> Iterator[Dict[str, Any]]:
    """生成合成数据：约 bad_ratio 比例的行带各类质量问题。"""
    rng = random.Random(seed)
    for i in range(n):
        row: Dict[str, Any] = {
            "id": i,
            "age": int(rng.gauss(35, 10)),
            "channel": rng.choice(CATEGORIES),
            "score": round(rng.uniform(0, 100), 2),
        }
        r = rng.random()
        if r < bad_ratio / 4:
            row["age"] = None            # 缺失
        elif r < bad_ratio / 2:
            row["id"] = i - 1            # 重复
        elif r < bad_ratio * 3 / 4:
            row["age"] = 999             # 超范围
        elif r < bad_ratio:
            row["score"] = "N/A"         # 类型错误
        yield row


def _rules(baseline_path: str) -> Dict[str, Any]:
    return {
        "rules": [
            {"id": "c1", "type": "completeness", "field": "age"},
            {"id": "u1", "type": "uniqueness", "fields": ["id"]},
            {"id": "r1", "type": "range", "field": "age", "min": 0, "max": 120},
            {"id": "t1", "type": "type_consistency", "field": "score", "expected": "number"},
            {"id": "d1", "type": "drift", "field": "channel", "baseline": baseline_path},
        ]
    }


def _time_run(rules_config: Dict[str, Any], rows: List[Dict[str, Any]]) -> float:
    engine = Engine(rules_config, base_dir=".")
    start = time.perf_counter()
    engine.run(iter(rows))
    return time.perf_counter() - start


def run_bench(sizes: List[int]) -> str:
    tmpdir = tempfile.mkdtemp(prefix="dq_bench_")
    baseline_path = os.path.join(tmpdir, "channel.baseline.json")
    ref_values = [row["channel"] for row in gen_rows(100_000, bad_ratio=0.0, seed=7)]
    save_baseline(build_baseline(ref_values, field="channel", kind="categorical"),
                  baseline_path)

    rules = _rules(baseline_path)["rules"]
    single_rules = {r["type"]: {"rules": [r]} for r in rules}
    all_rules = {"rules": rules}

    header = ["行数", "完整性", "唯一性", "范围", "类型", "漂移", "五类组合", "组合吞吐(行/秒)"]
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    for n in sizes:
        rows = list(gen_rows(n))
        timings: List[float] = []
        for rule_type in ["completeness", "uniqueness", "range", "type_consistency", "drift"]:
            timings.append(_time_run(single_rules[rule_type], rows))
        combined = _time_run(all_rules, rows)
        throughput = int(n / combined) if combined > 0 else 0
        cells = [f"{n:,}"] + [f"{t * 1000:.1f}ms" for t in timings]
        cells.append(f"{combined * 1000:.1f}ms")
        cells.append(f"{throughput:,}")
        lines.append("| " + " | ".join(cells) + " |")
        del rows
    return "\n".join(lines)


if __name__ == "__main__":
    print(run_bench([1, 1_000, 10_000, 100_000, 1_000_000]))
