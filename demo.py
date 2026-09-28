"""Scenario demo: split quality + completion-time distribution + retry data.

Runs purely on the executor's virtual clock, so it is instant and
deterministic. Writes machine-readable data to reports/ and prints human
readable tables.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

from shardlib import (
    Executor,
    Failure,
    Item,
    distribution,
    split_cost_balanced,
    split_even,
    split_quality,
)

REPORT_DIR = Path(__file__).parent / "reports"
WORKERS = 8
K = 8


class ScriptedHandler:
    """Fails on a fixed (item_id, attempt) schedule; duration from payload."""

    def __init__(self, fail_schedule, dead_ids=()):
        self.fail_schedule = {
            item_id: set(attempts) for item_id, attempts in fail_schedule.items()
        }
        self.dead_ids = set(dead_ids)

    def __call__(self, item, attempt):
        duration = float(item.payload if item.payload is not None else item.cost)
        if item.item_id in self.dead_ids or attempt in self.fail_schedule.get(item.item_id, ()):
            return Failure(error=f"fail@{attempt}", spent_work=duration)
        return duration


def items_from_costs(costs, durations=None, prefix="i"):
    durations = durations or costs
    return [
        Item(f"{prefix}{idx}", cost=float(cost), payload=float(dur))
        for idx, (cost, dur) in enumerate(zip(costs, durations))
    ]


def run_case(name, items, fail_schedule=None, dead_ids=(), late_items=(),
             k=K, workers=WORKERS, max_attempts=3, compare_even=False):
    handler = ScriptedHandler(fail_schedule or {}, dead_ids)
    result = Executor(
        handler,
        shard_count=k,
        workers=workers,
        max_item_attempts=max_attempts,
    ).run(items, late_items=list(late_items))

    balanced_shards = split_cost_balanced(items, k)
    case = {
        "name": name,
        "task_count": len(items),
        "shards_requested": k,
        "workers": workers,
        "split_quality_balanced": split_quality(balanced_shards),
        "makespan": result.makespan,
        "wasted_work_on_failed_attempts": result.wasted_work,
        "succeeded": len(result.succeeded),
        "dead": len(result.dead),
        "resplit_events": len(result.resplits),
        "dynamically_added": len(result.added_item_ids),
        "final_progress": result.final_fraction,
        "item_completion_time": distribution(list(result.item_finish_tick.values())),
        "shard_duration": distribution(
            [rec.duration for rec in result.shard_records if rec.end_tick is not None]
        ),
        "worker_busy_time": result.worker_busy_time,
        "progress_series": [
            {"tick": s.tick, "fraction": s.fraction,
             "completed_work": s.completed_work, "total_work": s.total_work}
            for s in result.snapshots
        ],
        "resplits": result.resplits,
        "invocations": result.invocations,
    }
    if compare_even:
        even_shards = split_even(items, k)
        case["split_quality_even"] = split_quality(even_shards)
    return case


def fmt(v):
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def print_case(case):
    q = case["split_quality_balanced"]
    print(f"\n=== {case['name']} ===")
    print(f"tasks={case['task_count']} requested_shards={case['shards_requested']} "
          f"workers={case['workers']}")
    print(f"  split quality (cost-balanced): max_load={fmt(q['max_load'])} "
          f"mean={fmt(q['mean_load'])} std={fmt(q['std_load'])} "
          f"imbalance={fmt(q['imbalance_ratio'])} utilisation_lb={fmt(q['utilisation_lower_bound'])}")
    if "split_quality_even" in case:
        qe = case["split_quality_even"]
        print(f"  split quality (even chunks): max_load={fmt(qe['max_load'])} "
              f"mean={fmt(qe['mean_load'])} std={fmt(qe['std_load'])} "
              f"imbalance={fmt(qe['imbalance_ratio'])} utilisation_lb={fmt(qe['utilisation_lower_bound'])}")
    d = case["item_completion_time"]
    print(f"  item completion ticks: n={int(d['count'])} min={fmt(d['min'])} "
          f"p50={fmt(d['p50'])} p90={fmt(d['p90'])} p99={fmt(d['p99'])} max={fmt(d['max'])} "
          f"mean={fmt(d['mean'])} std={fmt(d['std'])}")
    sd = case["shard_duration"]
    print(f"  shard durations:       n={int(sd['count'])} min={fmt(sd['min'])} "
          f"p50={fmt(sd['p50'])} p90={fmt(sd['p90'])} max={fmt(sd['max'])} "
          f"mean={fmt(sd['mean'])} std={fmt(sd['std'])}")
    print(f"  makespan={fmt(case['makespan'])} wasted_work={fmt(case['wasted_work_on_failed_attempts'])} "
          f"succeeded={case['succeeded']} dead={case['dead']} "
          f"resplits={case['resplit_events']} added={case['dynamically_added']} "
          f"final_progress={fmt(case['final_progress'])}")
    busy = case["worker_busy_time"]
    print(f"  worker busy ticks: {[round(b, 2) for b in busy]}")


def main():
    random.seed(42)
    REPORT_DIR.mkdir(exist_ok=True)
    cases = []

    # 1. Skewed real-world-ish batch (lognormal costs), compare vs even cut.
    costs = [round(random.lognormvariate(2.5, 0.9), 2) for _ in range(200)]
    cases.append(run_case("1. mixed workload (n=200, lognormal costs)",
                          items_from_costs(costs), compare_even=True))

    # 2. Shards > tasks.
    tiny = items_from_costs([7.0, 3.0], prefix="t")
    cases.append(run_case("2. shards(16) > tasks(2)", tiny, k=16, workers=4))

    # 3. Single extremely long item among many short ones.
    giant_costs = [500.0] + [2.0] * 63
    cases.append(run_case("3. single extremely long item (500 vs 2x63)",
                          items_from_costs(giant_costs)))

    # 4. Everything fails.
    all_fail = items_from_costs([5.0] * 24, prefix="f")
    cases.append(run_case("4. all items fail", all_fail,
                          dead_ids={f"f{i}" for i in range(24)}))

    # 5. Partial failures: some items need several attempts (re-split path).
    flaky_costs = [10.0] * 16
    schedule = {"i3": {1, 2}, "i10": {1}, "i15": {1, 2, 3}}
    cases.append(run_case("5. partial failures + re-split (max 4 attempts)",
                          items_from_costs(flaky_costs), fail_schedule=schedule,
                          max_attempts=4))

    # 6. Dynamic additions: second batch arrives at tick 30.
    base = items_from_costs([8.0] * 16, prefix="b")
    late = [(30.0, items_from_costs([12.0] * 8, prefix="n"))]
    cases.append(run_case("6. dynamic batch added at tick 30", base,
                          late_items=late))

    for case in cases:
        print_case(case)

    out = REPORT_DIR / "demo_data.json"
    out.write_text(json.dumps({"workers": WORKERS, "cases": cases}, indent=2))

    # CSV-style summaries for quick inspection.
    with open(REPORT_DIR / "split_quality.csv", "w") as fh:
        fh.write("case,strategy,max_load,mean_load,std,imbalance,utilisation_lb\n")
        for case in cases:
            qb = case["split_quality_balanced"]
            fh.write(f"{case['name']},cost_balanced,{qb['max_load']:.4f},"
                     f"{qb['mean_load']:.4f},{qb['std_load']:.4f},"
                     f"{qb['imbalance_ratio']:.4f},{qb['utilisation_lower_bound']:.4f}\n")
            if "split_quality_even" in case:
                qe = case["split_quality_even"]
                fh.write(f"{case['name']},even_chunks,{qe['max_load']:.4f},"
                         f"{qe['mean_load']:.4f},{qe['std_load']:.4f},"
                         f"{qe['imbalance_ratio']:.4f},{qe['utilisation_lower_bound']:.4f}\n")

    with open(REPORT_DIR / "completion_time.csv", "w") as fh:
        fh.write("case,count,min,p50,mean,p90,p99,max,std,makespan,wasted_work\n")
        for case in cases:
            d = case["item_completion_time"]
            fh.write(
                f"{case['name']},{int(d['count'])},{d['min']:.3f},{d['p50']:.3f},"
                f"{d['mean']:.3f},{d['p90']:.3f},{d['p99']:.3f},{d['max']:.3f},"
                f"{d['std']:.3f},{case['makespan']:.3f},{case['wasted_work_on_failed_attempts']:.3f}\n"
            )

    print(f"\nWrote {out}, split_quality.csv, completion_time.csv")


if __name__ == "__main__":
    main()
