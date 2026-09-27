"""命令行入口：

  python -m dq.cli check    --data data.jsonl --rules rules.json [--report out.json]
  python -m dq.cli baseline --data ref.jsonl  --field age --output baselines/age.json
  python -m dq.cli bench    [--sizes 1,1000,100000,1000000]

退出码：check 子命令在整体状态为 fail/error 时返回 1，便于接入入库前门禁。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from typing import Any, Dict, Iterable, Iterator, List

from .drift import build_baseline, save_baseline
from .engine import Engine
from .report import render_text


def load_rows(path: str) -> Iterator[Dict[str, Any]]:
    """按扩展名读取 JSONL / CSV，产出 dict 行（流式）。"""
    if path.endswith(".jsonl"):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)
    elif path.endswith(".csv"):
        with open(path, "r", encoding="utf-8", newline="") as fh:
            yield from csv.DictReader(fh)
    else:
        raise ValueError(f"不支持的数据格式: {path}（仅支持 .jsonl / .csv）")


def cmd_check(args: argparse.Namespace) -> int:
    with open(args.rules, "r", encoding="utf-8") as fh:
        rules_config = json.load(fh)
    import os

    engine = Engine(rules_config, base_dir=os.path.dirname(os.path.abspath(args.rules)))
    report = engine.run(load_rows(args.data))
    print(render_text(report))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(report.to_json())
        print(f"\nJSON 报告已写入 {args.report}")
    return 1 if report.status in ("fail", "error") else 0


def cmd_baseline(args: argparse.Namespace) -> int:
    values = (row.get(args.field) for row in load_rows(args.data))
    baseline = build_baseline(
        values,
        field=args.field,
        bins=args.bins,
        kind=args.kind,
        max_age_days=args.max_age_days,
        top_k=args.top_k,
    )
    save_baseline(baseline, args.output)
    print(f"基线已写入 {args.output}：字段={args.field} 类型={baseline['kind']} "
          f"样本={baseline['total']} 分箱数={len(baseline['bin_counts'])}")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    from bench.bench import run_bench

    sizes = [int(s) for s in args.sizes.split(",") if s]
    print(run_bench(sizes))
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dq", description="入库前数据质量检查")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="按规则配置检查数据")
    p_check.add_argument("--data", required=True, help="数据文件（.jsonl / .csv）")
    p_check.add_argument("--rules", required=True, help="规则配置 JSON")
    p_check.add_argument("--report", help="JSON 报告输出路径")
    p_check.set_defaults(func=cmd_check)

    p_base = sub.add_parser("baseline", help="从参考数据构建漂移基线快照")
    p_base.add_argument("--data", required=True, help="参考数据文件（.jsonl / .csv）")
    p_base.add_argument("--field", required=True, help="字段名")
    p_base.add_argument("--output", required=True, help="基线快照输出路径")
    p_base.add_argument("--bins", type=int, default=10, help="数值分箱数（默认 10）")
    p_base.add_argument("--kind", default="auto", choices=["auto", "numeric", "categorical"])
    p_base.add_argument("--max-age-days", type=int, default=None, help="基线有效期（天）")
    p_base.add_argument("--top-k", type=int, default=50, help="类别字段保留的高频类别数")
    p_base.set_defaults(func=cmd_baseline)

    p_bench = sub.add_parser("bench", help="性能基准（1 条到百万条）")
    p_bench.add_argument("--sizes", default="1,1000,10000,100000,1000000")
    p_bench.set_defaults(func=cmd_bench)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
