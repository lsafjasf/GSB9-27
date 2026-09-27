"""自测：构造含敏感字段的样例，验证漏网为零、留痕完整、摘要可校验。

运行：python3 selftest.py
产物：artifacts/leak_check.json  artifacts/audit_sample.jsonl  artifacts/redacted_sample.json
"""
import json
import os
import sys

from redact import Redactor, find_leaks, digest_of

ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")

# ---- 明文秘密清单（漏网检查的基准）----
SECRETS = {
    "db_password": "Sup3r$ecret!2026",
    "nested_api_key": "sk-live-9f8e7d6c5b4a39281706",
    "array_token": "tok_array_element_000123456",
    "renamed_pwd": "p@ssw0rd_renamed_field",
    "camel_secret": "CamelCaseSecretValue!42",
    "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c",
    "bearer_raw": "abcdef1234567890XYZW",
    "freetext_pwd": "fr33text_pass!",
    "kv_token": "kvtoken_9988776655",
    "email": "zhang.san@example-corp.com",
    "mobile": "13812345678",
    "idcard": "110101199003077758",
    "bankcard": "6222020200112233445",
    "aws_akid": "AKIAIOSFODNN7EXAMPLE",
    "longline_secret": "longline_hidden_secret_0xDEAD",
    "embedded_json_pwd": "embedded_json_pw_777",
    "cred_user": "cred_child_value_xyz",
}

# 校验身份证号/银行卡号是通过校验位的合法样本
def _fix_idcard(prefix17):
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    codes = "10X98765432"
    return prefix17 + codes[sum(int(prefix17[i]) * weights[i] for i in range(17)) % 11]

def _fix_luhn(prefix):
    total = 0
    body = prefix + "0"
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return prefix + str((10 - total % 10) % 10)

SECRETS["idcard"] = _fix_idcard("11010119900307775")
SECRETS["bankcard"] = _fix_luhn("622202020011223344")

LONG_LINE = ("padding-" + "x" * 64 + " ") * 3000 + \
    " password=" + SECRETS["longline_secret"] + " " + \
    SECRETS["jwt"] + " " + ("tail-" + "y" * 64 + " ") * 3000

SAMPLE = {
    "ts": "2026-09-28T10:00:00+08:00",
    "level": "INFO",
    "service": "billing",
    "user": {
        "name": "zhangsan",
        "email": SECRETS["email"],
        "mobile": SECRETS["mobile"],
        "national_id_note": "note:" + SECRETS["idcard"],
        "credentials": {"username": "u1", "password": SECRETS["db_password"]},
    },
    "db": {"host": "10.0.0.8", "password": SECRETS["db_password"]},
    "config": {"nested": {"deep": {"api_key": SECRETS["nested_api_key"]}}},
    "tokens": ["ok-value", {"refreshToken": SECRETS["array_token"]}, SECRETS["jwt"]],
    # 字段名被改写的形式
    "x_pwd_bak": SECRETS["renamed_pwd"],
    "userPassword2": SECRETS["renamed_pwd"],
    "CamelCaseSecretKey": SECRETS["camel_secret"],
    "authTokenValue": SECRETS["array_token"],
    # 自由文本中的敏感值
    "message": ("login failed for " + SECRETS["email"] + " mobile=" + SECRETS["mobile"] +
                " password=" + SECRETS["freetext_pwd"] + " token: " + SECRETS["kv_token"] +
                " auth Bearer " + SECRETS["bearer_raw"]),
    "headers": {"Authorization": "Bearer " + SECRETS["bearer_raw"], "x-aws-key": SECRETS["aws_akid"]},
    "payment": {"card": SECRETS["bankcard"], "amount": 19900},
    # 内嵌 JSON 字符串里的口令
    "raw_event": json.dumps({"event": "reset", "password": SECRETS["embedded_json_pwd"]},
                            ensure_ascii=False),
    # 敏感字段名下挂子树（整棵子树按一个值脱敏）
    "credential_bundle": {"child": SECRETS["cred_user"], "n": 1},
    # 超长日志行
    "long_line": LONG_LINE,
    # 非敏感字段不应被误伤
    "author": "should-stay",
    "monkey_business": "should-stay",
    "tokenize_count": 42,
}


def main():
    redactor = Redactor()
    redacted = redactor.redact(SAMPLE)
    serialized = json.dumps(redacted, ensure_ascii=False)

    # 1) 漏网检查：所有明文秘密在脱敏结果（结构 + 序列化文本）中必须零残留
    leaks = find_leaks(redacted, SECRETS.values())
    leaks_ser = [s for s in SECRETS.values() if s in serialized]

    # 2) 摘要可校验：每个秘密的 sha256 前 12 位应出现在脱敏结果中。
    # 敏感字段名下挂子树时，摘要针对子树的规范化 JSON。
    digest_source = {
        "cred_user": json.dumps({"child": SECRETS["cred_user"], "n": 1},
                                ensure_ascii=False, sort_keys=True),
    }
    digest_check = {name: (digest_of(digest_source.get(name, v)) in serialized)
                    for name, v in SECRETS.items()}

    # 3) 留痕完整性：每条秘密至少对应一条留痕，且留痕含路径与规则
    audit = redactor.audit_dicts()
    audit_digests = {r["digest"] for r in audit}
    audit_check = {name: (digest_of(digest_source.get(name, v)) in audit_digests)
                   for name, v in SECRETS.items()}
    audit_wellformed = all(
        r["path"] and r["rule"] and len(r["digest"]) == 12 and r["value_length"] > 0
        for r in audit)

    # 4) 误伤检查：非敏感字段保持原样
    false_pos = {
        "author": redacted["author"] != "should-stay",
        "monkey_business": redacted["monkey_business"] != "should-stay",
        "tokenize_count": redacted["tokenize_count"] != 42,
    }

    report = {
        "secrets_total": len(SECRETS),
        "leak_count": len(leaks) + len(leaks_ser),
        "leaks": [{"path": p, "secret": s} for p, s in leaks] +
                 [{"path": "<serialized>", "secret": s} for s in leaks_ser],
        "digest_verifiable": digest_check,
        "audit_covers_secret": audit_check,
        "audit_record_count": len(audit),
        "audit_wellformed": audit_wellformed,
        "false_positives": false_pos,
        "passed": (not leaks and not leaks_ser and all(digest_check.values())
                   and all(audit_check.values()) and audit_wellformed
                   and not any(false_pos.values())),
    }

    os.makedirs(ART, exist_ok=True)
    with open(os.path.join(ART, "leak_check.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(os.path.join(ART, "audit_sample.jsonl"), "w", encoding="utf-8") as f:
        for r in audit:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(ART, "redacted_sample.json"), "w", encoding="utf-8") as f:
        json.dump(redacted, f, ensure_ascii=False, indent=2)

    print(f"secrets={report['secrets_total']} leaks={report['leak_count']} "
          f"audit_records={len(audit)} passed={report['passed']}")
    if not report["passed"]:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(1)
    print("SELFTEST OK: 漏网为零，留痕完整，摘要可校验，无误伤。")


if __name__ == "__main__":
    main()
