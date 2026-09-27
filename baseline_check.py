#!/usr/bin/env python3
"""安全基线检查脚本（仅标准库）。

核心原则：只对「合并全部来源后的最终生效值」下结论，绝不对单个文件下结论。
每个判定输出：基线要求、实际值、来源链、严重级别；不满足时给出修复建议。

用法:
    python3 baseline_check.py --baseline baseline.json \
        --source defaults=examples/sources/defaults.json \
        --source system=examples/sources/system.json \
        --source app=examples/sources/app.json \
        --source local=examples/sources/local.json

--source 后面的顺序即优先级顺序，后者覆盖前者（深度合并）。
退出码: 0=全部通过(允许 warning 级不合规), 1=存在 error/critical 级不合规或字段缺失,
2=用法/输入错误。
"""
import argparse
import datetime
import json
import sys

SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}
FAIL_EXIT_SEVERITY = "error"  # 达到该级别即退出码 1


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SystemExit(f"错误: 文件不存在: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"错误: JSON 解析失败 {path}: {exc}")


def merge_sources(sources):
    """按顺序深度合并 (名称, dict) 列表，返回 (合并结果, 来源链)。

    来源链: {键路径元组: [(来源名, 该来源设置的值), ...]}，按应用顺序排列，
    最后一项即最终生效值的来源。
    """
    merged = {}
    provenance = {}

    def merge_into(dst, src, name, path):
        for key, value in src.items():
            cur = path + (key,)
            if isinstance(value, dict) and value:
                if not isinstance(dst.get(key), dict):
                    # 标量覆盖子树：作废旧子树的来源记录
                    dst[key] = {}
                    for old in [p for p in provenance if p[: len(cur)] == cur]:
                        del provenance[old]
                merge_into(dst[key], value, name, cur)
            else:
                if isinstance(dst.get(key), dict):
                    # 标量覆盖子树：作废旧子树的来源记录
                    for old in [p for p in provenance if p[: len(cur)] == cur]:
                        del provenance[old]
                dst[key] = value
                provenance.setdefault(cur, []).append((name, value))

    for name, data in sources:
        if not isinstance(data, dict):
            raise SystemExit(f"错误: 来源 {name} 的顶层必须是 JSON 对象")
        merge_into(merged, data, name, ())
    return merged, provenance


def get_path(data, path):
    node = data
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def compare(op, actual, expect):
    """返回 (是否通过, 说明)。类型不匹配一律判不通过并说明原因。"""
    exp = json.dumps(expect, ensure_ascii=False)
    try:
        if op == "eq":
            return actual == expect, f"必须等于 {exp}"
        if op == "ne":
            return actual != expect, f"不得等于 {exp}"
        if op == "in":
            return actual in expect, f"必须属于 {exp}"
        if op == "not_in":
            return actual not in expect, f"不得属于 {exp}"
        if op == "ge":
            if isinstance(actual, bool) or not isinstance(actual, (int, float)):
                return False, f"必须 >= {exp}（实际值不是数值）"
            return actual >= expect, f"必须 >= {exp}"
        if op == "le":
            if isinstance(actual, bool) or not isinstance(actual, (int, float)):
                return False, f"必须 <= {exp}（实际值不是数值）"
            return actual <= expect, f"必须 <= {exp}"
    except TypeError:
        return False, f"无法比较（类型不匹配）"
    raise SystemExit(f"错误: 未知的比较操作符: {op}")


def version_tuple(text):
    return tuple(int(p) for p in str(text).split("."))


def evaluate(baseline, merged, provenance, today):
    """返回 (findings 列表, 基线过期信息或 None)。"""
    findings = []
    base_version = baseline["version"]

    expired = None
    valid_until = baseline.get("valid_until")
    if valid_until:
        until = datetime.date.fromisoformat(valid_until)
        if today > until:
            expired = {
                "valid_until": valid_until,
                "days_overdue": (today - until).days,
            }

    for rule in baseline["rules"]:
        key = rule["key"]
        path = tuple(key.split("."))
        since = rule.get("since", base_version)
        # 规则随当前基线版本引入 => 不合规属于「新引入」，否则为「历史存量」
        category = "新引入" if version_tuple(since) >= version_tuple(base_version) else "历史存量"

        actual, present = get_path(merged, path)
        chain = provenance.get(path, [])

        if not present:
            findings.append({
                "id": rule.get("id", key),
                "key": key,
                "status": "MISSING",
                "severity": rule.get("missing_severity", rule["severity"]),
                "category": category,
                "requirement": rule["requirement"],
                "actual": None,
                "chain": [],
                "remediation": rule["remediation"],
                "note": "所有配置来源均未提供该字段",
            })
            continue

        ok, op_desc = compare(rule["op"], actual, rule.get("expect"))
        findings.append({
            "id": rule.get("id", key),
            "key": key,
            "status": "PASS" if ok else "FAIL",
            "severity": rule["severity"],
            "category": category,
            "requirement": f"{rule['requirement']}（{op_desc}）",
            "actual": actual,
            "chain": list(chain),
            "remediation": rule["remediation"],
            "note": "",
        })
    return findings, expired


def format_chain(chain):
    parts = []
    for idx, (name, value) in enumerate(chain):
        label = f"{name}={json.dumps(value, ensure_ascii=False)}"
        if idx == len(chain) - 1:
            label += " (生效)"
        parts.append(label)
    return " -> ".join(parts)


def render_text(baseline, findings, expired, source_names):
    lines = []
    lines.append("=" * 68)
    lines.append(f"安全基线检查报告  基线: {baseline['name']} v{baseline['version']}")
    lines.append(f"配置来源(优先级从低到高): {' -> '.join(source_names)}")
    if expired:
        lines.append(
            f"[警告] 基线已过期: 有效期至 {expired['valid_until']}，"
            f"已超期 {expired['days_overdue']} 天，请更新基线后再据此结论上线。"
        )
    lines.append("=" * 68)

    order = {"critical": 0, "error": 1, "warning": 2, "info": 3}
    problems = [f for f in findings if f["status"] != "PASS"]
    problems.sort(key=lambda f: (order.get(f["severity"], 9), f["key"]))
    passed = [f for f in findings if f["status"] == "PASS"]

    for f in problems:
        lines.append("")
        lines.append(f"[{f['status']}][{f['severity']}][{f['category']}] {f['key']}")
        lines.append(f"  基线要求: {f['requirement']}")
        if f["status"] == "MISSING":
            lines.append("  实际值:   <缺失>（合并全部来源后仍无此字段）")
            lines.append("  来源链:   <无>")
        else:
            lines.append(f"  实际值:   {json.dumps(f['actual'], ensure_ascii=False)}")
            lines.append(f"  来源链:   {format_chain(f['chain'])}")
        if f["note"]:
            lines.append(f"  说明:     {f['note']}")
        lines.append(f"  修复建议: {f['remediation']}")

    lines.append("")
    lines.append("-" * 68)
    lines.append(f"通过 {len(passed)} 项 / 共 {len(findings)} 项；"
                 f"不合规 {len(problems)} 项"
                 f"（新引入 {sum(1 for f in problems if f['category'] == '新引入')}，"
                 f"历史存量 {sum(1 for f in problems if f['category'] == '历史存量')}）")
    if passed:
        lines.append("通过项: " + ", ".join(f["key"] for f in passed))
    return "\n".join(lines) + "\n"


def render_json(baseline, findings, expired, source_names):
    return json.dumps({
        "baseline": {
            "name": baseline["name"],
            "version": baseline["version"],
            "valid_until": baseline.get("valid_until"),
            "expired": expired,
        },
        "sources": source_names,
        "findings": [
            {**f, "chain": [{"source": n, "value": v} for n, v in f["chain"]]}
            for f in findings
        ],
    }, ensure_ascii=False, indent=2) + "\n"


def parse_source(spec):
    if "=" in spec:
        name, path = spec.split("=", 1)
    else:
        name, path = spec.rsplit("/", 1)[-1], spec
    return name, path


def main(argv=None):
    parser = argparse.ArgumentParser(description="安全基线检查（按合并后的最终生效值判定）")
    parser.add_argument("--baseline", required=True, help="基线配置 JSON（含版本号与有效期）")
    parser.add_argument("--source", action="append", required=True,
                        help="配置来源，格式 [名称=]路径，可多次；后者优先级更高")
    parser.add_argument("--now", help="覆盖当前日期 YYYY-MM-DD（用于复现/测试）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式报告")
    parser.add_argument("-o", "--output", help="报告写入文件（默认打印到 stdout）")
    args = parser.parse_args(argv)

    baseline = load_json(args.baseline)
    for field in ("name", "version", "rules"):
        if field not in baseline:
            raise SystemExit(f"错误: 基线文件缺少字段: {field}")

    sources = []
    source_names = []
    for spec in args.source:
        name, path = parse_source(spec)
        sources.append((name, load_json(path)))
        source_names.append(name)

    merged, provenance = merge_sources(sources)
    today = (datetime.date.fromisoformat(args.now) if args.now
             else datetime.date.today())
    findings, expired = evaluate(baseline, merged, provenance, today)

    report = (render_json if args.json else render_text)(
        baseline, findings, expired, source_names)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(report)
    else:
        sys.stdout.write(report)

    threshold = SEVERITY_ORDER[FAIL_EXIT_SEVERITY]
    worst = max((SEVERITY_ORDER.get(f["severity"], 0)
                 for f in findings if f["status"] != "PASS"), default=-1)
    return 1 if worst >= threshold else 0


if __name__ == "__main__":
    sys.exit(main())
