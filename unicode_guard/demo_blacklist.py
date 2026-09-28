# -*- coding: utf-8 -*-
"""端到端演示：用 canonical_key 做黑名单/占用名校验。

运行: python3 demo_blacklist.py
"""

from unicode_guard import Config, Policy, analyze, canonical_key


BLACKLIST = ["admin", "root", "system", "test"]
TAKEN = ["alice", "bob"]

blocked = {canonical_key(x) for x in BLACKLIST}
taken = {canonical_key(x) for x in TAKEN}

ATTEMPTS = [
    "admin", "Admin", "ADMIN",          # 大小写
    "аdmin", "аdmіn",                  # 西里尔同形
    "rοοt",                            # 希腊同形
    "ѕуѕtеm",                        # 希腊全词
    "adm\u200Bin",               # 零宽插入
    "аdm\u200Bin",             # 同形+零宽
    "alice", "ALICE",                  # 占用名
    "café", "café",              # 组合顺序调换
    "normal_user",                     # 正常用户
]

# 生产配置：不可见字符直接拒绝；同形/混用先告警并走黑名单判定
cfg = Config(invisible_policy=Policy.REJECT,
             confusable_policy=Policy.WARN,
             mixed_script_policy=Policy.WARN)


def validate(username: str) -> str:
    report = analyze(username, cfg)
    if not report.ok:
        return f"拒绝(安全策略): {[i.kind.value for i in report.issues]}"
    key = report.canonical
    if key in blocked:
        return f"拒绝(黑名单命中 key={key!r})"
    if key in taken:
        return f"拒绝(用户名已占用 key={key!r})"
    warn = [i.kind.value for i in report.issues]
    suffix = f"，告警={warn}" if warn else ""
    return f"通过(存储={report.storage!r}, key={key!r}{suffix})"


def main() -> None:
    for name in ATTEMPTS:
        print(f"{name!r:<24} -> {validate(name)}")


if __name__ == "__main__":
    main()
