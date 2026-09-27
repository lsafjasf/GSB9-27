"""CLI: python3 -m diffignore OLD.json NEW.json [--rules rules.json] [--audit FILE|-] [--format text|json]"""
import argparse
import json
import sys

from .core import apply_rules, diff, format_audit, load_rules_file, result_to_jsonable


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="diffignore",
        description="结构化比较两份 JSON 配置/数据文件，支持可审计的忽略规则",
    )
    p.add_argument("old", help="旧文件 (JSON)")
    p.add_argument("new", help="新文件 (JSON)")
    p.add_argument("--rules", help="忽略规则文件 (JSON)")
    p.add_argument(
        "--audit",
        help="审计输出路径；'-' 表示标准输出（text 模式下默认即打印到标准输出）",
    )
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--warn-abs", type=int, default=None, help="单规则屏蔽条数告警阈值")
    p.add_argument("--warn-pct", type=float, default=None, help="单规则屏蔽比例告警阈值 (0-1)")
    args = p.parse_args(argv)

    with open(args.old, encoding="utf-8") as f:
        old = json.load(f)
    with open(args.new, encoding="utf-8") as f:
        new = json.load(f)

    rules, opts = [], {}
    if args.rules:
        rules, opts = load_rules_file(args.rules)
    warn_abs = args.warn_abs if args.warn_abs is not None else opts.get("warn_abs", 100)
    warn_pct = args.warn_pct if args.warn_pct is not None else opts.get("warn_pct", 0.5)

    raw = diff(old, new)
    result = apply_rules(raw, rules, warn_abs=warn_abs, warn_pct=warn_pct)

    if args.format == "json":
        out = json.dumps(result_to_jsonable(result), ensure_ascii=False, indent=2)
        _write(args.audit, out)
    else:
        lines = [f"== 剩余差异（{len(result.kept)} 条，已屏蔽 {len(result.masked)} 条）=="]
        lines.extend(d.render() for d in result.kept)
        lines.append("")
        lines.append(format_audit(result))
        _write(args.audit, "\n".join(lines))

    return 1 if result.kept else 0


def _write(dest, text):
    if dest and dest != "-":
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    sys.exit(main())
