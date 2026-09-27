"""自测：漏网检查 + 留痕样例 + 性能对比。仅标准库。

运行：python3 selftest.py
产物：evidence/leak_check.json  evidence/audit_trail_sample.json  evidence/perf_report.json
退出码非零即存在漏网或校验失败。
"""

import copy
import json
import os
import sys
import time

import redact

EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence")

# ---- 测试用敏感明文（身份证/银行卡带合法校验位，确保内容规则命中）----
SECRETS = {
    "password": "hunter2!Xy",
    "token": "tok_live_9f8e7d6c5b4a",
    "api_key": "ak-abcdef1234567890",
    "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c",
    "bearer": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c",
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "email": "zhang.san@example.com",
    "phone": "13812345678",
    "idcard": "110101199003077897",
    "bankcard": "6222020200112230",
    "kv_password": "hunter2!Xy",
    "pem": "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7\n-----END RSA PRIVATE KEY-----",
    "free_text_idcard": "110101199003077897",
}


def build_sample():
    """构造覆盖全部要求场景的样例日志。"""
    return {
        "ts": "2026-09-28T10:00:00+08:00",
        "event": "user.login",
        "user": {
            "name": "zhangsan",
            "password": SECRETS["password"],            # 字段名规则
            "email": SECRETS["email"],
            "contact": {"mobile": SECRETS["phone"]},    # 嵌套字段
        },
        "attempts": [                                    # 数组元素
            {"token": SECRETS["token"], "ok": False},
            {"apiKey": SECRETS["api_key"], "ok": True},
        ],
        "headers": {"Authorization": SECRETS["bearer"], "Cookie": "sid=abc123"},
        # 字段名被改写：字段名不在规则表内，靠内容特征兜底
        "credential": SECRETS["jwt"],
        "note": "operator reset password: {} please rotate".format(SECRETS["kv_password"]),
        "remark": "联系 {} 或 {} 核实，身份证 {}".format(
            SECRETS["email"], SECRETS["phone"], SECRETS["idcard"]),
        "payment": {"card": SECRETS["bankcard"], "nested": [{"id_number": SECRETS["idcard"]}]},
        "deploy": {"aws_key_hint": SECRETS["aws"], "key_material": SECRETS["pem"]},
        # 超长日志行
        "dump": "prefix-" + "A" * 6000 + "-token=" + SECRETS["token"],
    }


def walk_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def leak_check(sanitized, secrets):
    """漏网检查：任何明文出现在脱敏结果中即计为漏网。"""
    corpus = json.dumps(sanitized, ensure_ascii=False)
    results, leaked = [], 0
    for name, plain in secrets.items():
        hit = plain in corpus
        # 逐字符串复核，防止序列化差异造成假阴性
        if not hit:
            hit = any(plain in s for s in walk_strings(sanitized))
        results.append({"secret": name, "leaked": hit})
        leaked += int(hit)
    return {"total_secrets": len(secrets), "leaked": leaked, "detail": results}


def digest_verifiability(sanitized):
    """摘要可校验：脱敏结果中应能找到与明文对应的 sha256 前 12 位。"""
    corpus = json.dumps(sanitized, ensure_ascii=False)
    checks = {}
    for name in ("password", "token", "jwt", "idcard"):
        checks[name] = redact.digest12(SECRETS[name]) in corpus
    return checks


def perf_benchmark(n=2000):
    base = build_sample()
    entries = []
    for i in range(n):
        e = copy.deepcopy(base)
        e["seq"] = i
        entries.append(e)
    raw_lines = [
        '2026-09-28 INFO login user=zhangsan password={} ip=10.0.0.1 card={} {}'.format(
            SECRETS["password"], SECRETS["bankcard"], "x" * 5000)
        for _ in range(200)
    ]

    size_before = sum(len(json.dumps(e, ensure_ascii=False).encode()) for e in entries)
    size_before += sum(len(l.encode()) for l in raw_lines)

    t0 = time.perf_counter()
    trail = redact.Trail()
    out_entries = [redact.sanitize(e, redact.Trail()) for e in entries]
    out_lines = [redact.filter_line(l, redact.Trail()) for l in raw_lines]
    elapsed = time.perf_counter() - t0

    size_after = sum(len(json.dumps(e, ensure_ascii=False).encode()) for e in out_entries)
    size_after += sum(len(l.encode()) for l in out_lines)

    return {
        "entries": n,
        "raw_lines": len(raw_lines),
        "elapsed_sec": round(elapsed, 4),
        "entries_per_sec": round((n + len(raw_lines)) / elapsed, 1),
        "size_before_bytes": size_before,
        "size_after_bytes": size_after,
        "size_delta_pct": round((size_after - size_before) * 100.0 / size_before, 2),
    }


def main():
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    failures = []

    # 1) 结构化日志过滤 + 漏网检查
    sample = build_sample()
    trail = redact.Trail()
    sanitized = redact.sanitize(sample, trail)
    leak = leak_check(sanitized, SECRETS)
    if leak["leaked"] != 0:
        failures.append("结构化日志存在漏网: {}".format(leak["leaked"]))

    # 2) 原始日志行（非 JSON）过滤 + 漏网检查
    raw_line = ('2026-09-28 10:00:01 INFO auth ok user=zhangsan password={} '
                'Authorization: {} aws={} phone={} tail={}').format(
        SECRETS["password"], SECRETS["bearer"], SECRETS["aws"],
        SECRETS["phone"], "y" * 5000)
    line_trail = redact.Trail()
    clean_line = redact.filter_line(raw_line, line_trail)
    line_leaks = [k for k, v in SECRETS.items() if v in clean_line]
    if line_leaks:
        failures.append("原始日志行存在漏网: {}".format(line_leaks))

    # 3) 摘要可校验性
    digest_checks = digest_verifiability(sanitized)
    if not all(digest_checks.values()):
        failures.append("摘要不可校验: {}".format(digest_checks))

    # 4) 超长行确被截断
    if len(sanitized["dump"]) >= redact.MAX_STRING_LEN:
        failures.append("超长字段未被截断")
    if len(clean_line) >= len(raw_line):
        failures.append("超长日志行未被截断")

    # 5) 留痕完整性：每条留痕必须含路径/规则/动作/摘要
    for entry in trail.entries + line_trail.entries:
        if not all(entry.get(k) for k in ("path", "rule", "action", "sha256_12")):
            failures.append("留痕字段缺失: {}".format(entry))
            break
    rules_hit = sorted({e["rule"] for e in trail.entries + line_trail.entries})

    # 6) 性能对比
    perf = perf_benchmark()

    # ---- 证据落盘 ----
    leak_report = {
        "structured": leak,
        "raw_line": {"leaked_fields": line_leaks, "leaked": len(line_leaks)},
        "digest_verifiability": digest_checks,
        "conclusion": "PASS" if not failures else "FAIL",
    }
    with open(os.path.join(EVIDENCE_DIR, "leak_check.json"), "w", encoding="utf-8") as f:
        json.dump(leak_report, f, ensure_ascii=False, indent=2)

    trail_sample = {
        "structured_log": {"before": sample, "after": sanitized, "trail": trail.as_list()},
        "raw_line": {"before": raw_line[:600] + "...", "after": clean_line[:600] + ("..." if len(clean_line) > 600 else ""),
                     "trail": line_trail.as_list()},
    }
    with open(os.path.join(EVIDENCE_DIR, "audit_trail_sample.json"), "w", encoding="utf-8") as f:
        json.dump(trail_sample, f, ensure_ascii=False, indent=2)

    with open(os.path.join(EVIDENCE_DIR, "perf_report.json"), "w", encoding="utf-8") as f:
        json.dump(perf, f, ensure_ascii=False, indent=2)

    # ---- 控制台摘要 ----
    print("== 漏网检查 ==")
    print("结构化日志: {}/{} 条敏感明文被拦截, 漏网 {}".format(
        leak["total_secrets"] - leak["leaked"], leak["total_secrets"], leak["leaked"]))
    print("原始日志行: 漏网 {}".format(len(line_leaks)))
    print("摘要可校验: {}".format(digest_checks))
    print("== 留痕 ==")
    print("结构化 {} 条, 原始行 {} 条, 命中规则: {}".format(
        len(trail.entries), len(line_trail.entries), ", ".join(rules_hit)))
    print("== 性能 ==")
    print(json.dumps(perf, ensure_ascii=False, indent=2))

    if failures:
        print("FAIL:")
        for msg in failures:
            print(" -", msg)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
