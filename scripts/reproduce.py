#!/usr/bin/env python3
"""Reproduce FIM alert noise and compare the pre-fix and fixed monitors.

One deterministic workload is applied to a shared watch tree:

legitimate noise:
  * editor temp/swap/backup files, tmp scratch files
  * generated build artifacts (__pycache__, *.pyc/*.pyo, build/)
  * log rotation (app.log, app.log.1.gz, app.err.DATE.gz)
  * pre-declared configuration deploys (admitted updates)
  * benign permission-only changes
  * delete + recreate of a rebuilt cache
genuine attacks (all keep size and forge mtime):
  * in-place overwrite of etc/hosts
  * delete + recreate of etc/cron.d/job
  * atomic-rename replacement of usr/bin/healthcheck

Usage: python3 scripts/reproduce.py [--json] [--keep]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fim.baseline import NaiveMonitor  # noqa: E402
from fim.fmonitor import FixedMonitor  # noqa: E402

ATTACK_PATHS = ["etc/hosts", "etc/cron.d/job", "usr/bin/healthcheck"]

NOISE_LABELS = {
    "tmp/scratch.txt": "temp",
    "etc/.hosts.swp": "temp",
    "notes.txt.bak": "temp",
    "tmp/scratch2.tmp": "temp",
    "src/__pycache__/mod.cpython-312.pyc": "generated",
    "build/output.o": "generated",
    "src/app.pyo": "generated",
    "var/app.log": "logs",
    "var/app.log.1.gz": "logs",
    "var/app.err.2026-09-28.gz": "logs",
    "etc/app.conf": "legit-update",
    "bin/tool.sh": "perms",
    "var/state.cache": "legit-recreate",
}

CONF_V2 = "version=2\nfeature=true\n"
CONF_V3 = "version=3\nfeature=true\nretries=3\n"
ADMISSIONS_1 = [("etc/app.conf", CONF_V2.encode(), "deploy", "CR-1001")]
ADMISSIONS_2 = [
    ("etc/app.conf", CONF_V3.encode(), "deploy", "CR-1042"),
    ("var/state.cache", b"v=new\n", "ops", "CACHE-REBUILD-7"),
]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data, mode=None, mtime_ns=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode())
    if mode is not None:
        os.chmod(path, mode)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


def atomic_replace(path, data, mode=None, mtime_ns=None):
    tmp = path + ".deploy-tmp"
    with open(tmp, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode())
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


def same_length_evil(text):
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch not in "\r\n ":
            chars[i] = "z" if ch != "z" else "y"
            break
    return "".join(chars)


def build_tree(root):
    write(os.path.join(root, "etc/hosts"), "127.0.0.1 localhost\n")
    write(os.path.join(root, "etc/cron.d/job"), "0 * * * * /usr/bin/true\n")
    write(os.path.join(root, "usr/bin/healthcheck"), "#!/bin/sh\nexit 0\n", mode=0o755)
    write(os.path.join(root, "etc/app.conf"), "version=1\n")
    write(os.path.join(root, "bin/tool.sh"), "#!/bin/sh\necho ok\n", mode=0o755)
    write(os.path.join(root, "var/state.cache"), "v=old\n")


def apply_phase1(root):
    write(os.path.join(root, "tmp/scratch.txt"), "ephemeral\n")
    write(os.path.join(root, "etc/.hosts.swp"), "swap-bytes\n")
    write(os.path.join(root, "notes.txt.bak"), "old notes\n")
    write(os.path.join(root, "src/__pycache__/mod.cpython-312.pyc"), "BYTECODE0")
    write(os.path.join(root, "build/output.o"), "OBJECT0")
    write(os.path.join(root, "var/app.log"), "2026-09-28 boot ok\n")

    atomic_replace(
        os.path.join(root, "etc/app.conf"), CONF_V2,
        mode=os.stat(os.path.join(root, "etc/app.conf")).st_mode & 0o7777,
    )
    os.chmod(os.path.join(root, "bin/tool.sh"), 0o750)

    # attack 1: in-place overwrite, equal length, original mtime restored
    host = os.path.join(root, "etc/hosts")
    original = open(host, "r").read()
    mtime = os.stat(host).st_mtime_ns
    evil = same_length_evil(original)
    assert len(evil) == len(original) and evil != original
    with open(host, "w") as fh:
        fh.write(evil)
    os.utime(host, ns=(mtime, mtime))


def apply_phase2(root):
    write(os.path.join(root, "tmp/scratch2.tmp"), "more ephemeral\n")
    write(os.path.join(root, "src/app.pyo"), "BYTECODE1")
    write(os.path.join(root, "var/app.log.1.gz"), "ROTATED-GZ0")
    write(os.path.join(root, "var/app.err.2026-09-28.gz"), "ERR-GZ0")

    atomic_replace(
        os.path.join(root, "etc/app.conf"), CONF_V3,
        mode=os.stat(os.path.join(root, "etc/app.conf")).st_mode & 0o7777,
    )
    os.chmod(os.path.join(root, "bin/tool.sh"), 0o755)

    cache = os.path.join(root, "var/state.cache")
    os.remove(cache)
    write(cache, "v=new\n")

    # attack 2: delete + recreate, equal size, original mtime restored
    job = os.path.join(root, "etc/cron.d/job")
    original = open(job, "r").read()
    mtime = os.stat(job).st_mtime_ns
    os.remove(job)
    evil = same_length_evil(original)
    assert len(evil) == len(original)
    write(job, evil, mtime_ns=mtime)

    # attack 3: atomic-rename replacement, inode changes, mtime forged
    hc = os.path.join(root, "usr/bin/healthcheck")
    original = open(hc, "r").read()
    mtime = os.stat(hc).st_mtime_ns
    atomic_replace(hc, same_length_evil(original), mode=0o755, mtime_ns=mtime)


def admit_all(mon, admissions):
    for rel, content, operator, ticket in admissions:
        mon.admit(
            rel, operator=operator, reason="scheduled change",
            ticket=ticket, expected_digest=sha256_bytes(content),
        )


def evaluate(alerts):
    tp, fp = [], []
    buckets = {}
    for ev in alerts:
        (tp if ev.path in ATTACK_PATHS else fp).append(ev)
        if ev.path not in ATTACK_PATHS:
            label = NOISE_LABELS.get(ev.path, "other")
            buckets[label] = buckets.get(label, 0) + 1
    return tp, fp, buckets


def run():
    tmp = tempfile.mkdtemp(prefix="fim-repro-")
    root = os.path.join(tmp, "watch")
    state_dir = os.path.join(tmp, "state")
    os.makedirs(root)
    os.makedirs(state_dir)
    try:
        build_tree(root)

        naive = NaiveMonitor(root, os.path.join(state_dir, "naive-state.json"))
        fixed = FixedMonitor(root, os.path.join(state_dir, "fixed-state.json"))
        naive.baseline()
        fixed.baseline()

        admit_all(fixed, ADMISSIONS_1)
        apply_phase1(root)
        n1 = naive.scan().alerts
        f1 = fixed.scan()

        admit_all(fixed, ADMISSIONS_2)
        apply_phase2(root)
        n2 = naive.scan().alerts
        f2 = fixed.scan()

        naive_alerts = n1 + n2
        fixed_alerts = f1.alerts + f2.alerts
        fixed_audit = f1.audit + f2.audit

        n_tp, n_fp, n_buckets = evaluate(naive_alerts)
        f_tp, f_fp, f_buckets = evaluate(fixed_alerts)
        n_fn = len(ATTACK_PATHS) - len(n_tp)
        f_fn = len(ATTACK_PATHS) - len(f_tp)

        audit_kinds = {}
        rule_hits = []
        for ev in fixed_audit:
            audit_kinds[ev.kind] = audit_kinds.get(ev.kind, 0) + 1
            if ev.kind == "rule_hit":
                rule_hits.append((ev.path, ev.detail["rule"]))

        return {
            "workdir": tmp,
            "attacks": len(ATTACK_PATHS),
            "benign_noise_events": len(NOISE_LABELS),
            "naive": {
                "alerts": len(naive_alerts), "tp": len(n_tp), "fp": len(n_fp),
                "fn": n_fn,
                "fp_rate": len(n_fp) / len(naive_alerts) if naive_alerts else 0.0,
                "fn_rate": n_fn / len(ATTACK_PATHS),
                "fp_buckets": n_buckets,
                "alert_list": [(e.kind, e.path) for e in naive_alerts],
                "detected_attacks": sorted(e.path for e in n_tp),
                "missed_attacks": sorted(
                    p for p in ATTACK_PATHS if p not in {e.path for e in n_tp}
                ),
            },
            "fixed": {
                "alerts": len(fixed_alerts), "tp": len(f_tp), "fp": len(f_fp),
                "fn": f_fn,
                "fp_rate": len(f_fp) / len(fixed_alerts) if fixed_alerts else 0.0,
                "fn_rate": f_fn / len(ATTACK_PATHS),
                "audit_events": len(fixed_audit),
                "audit_kinds": audit_kinds,
                "rule_hits": rule_hits,
                "detected_attacks": sorted(e.path for e in f_tp),
            },
        }
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def print_report(r):
    n, f = r["naive"], r["fixed"]
    print("=" * 72)
    print(" FIM 噪声量化与修复前后对比（同一份工作负载，3 个真实篡改）")
    print("=" * 72)
    print(f"工作负载: {r['benign_noise_events']} 次合法变动, {r['attacks']} 次真实篡改\n")

    print("[修复前 NaiveMonitor] 仅依赖 size/mtime/mode，无排除规则")
    print(f"  总告警 {n['alerts']}  真阳性 {n['tp']}  误报 {n['fp']}  漏报 {n['fn']}")
    print(f"  误报率(噪声告警/总告警) = {n['fp_rate']:.0%}")
    print(f"  漏报率(漏检篡改/全部篡改) = {n['fn_rate']:.0%}")
    print("  噪声来源:")
    for label in ["temp", "generated", "logs", "legit-update", "perms", "legit-recreate", "other"]:
        if n["fp_buckets"].get(label):
            print(f"    - {label:15s} {n['fp_buckets'][label]}")
    print(f"  漏检: {', '.join(n['missed_attacks'])}")
    print("    （内容被替换但长度相同且 mtime 被还原，stat-only 实现无法识别）\n")

    print("[修复后 FixedMonitor] SHA-256 内容指纹 + 显式排除规则 + 更新准入")
    print(f"  总告警 {f['alerts']}  真阳性 {f['tp']}  误报 {f['fp']}  漏报 {f['fn']}")
    print(f"  误报率 = {f['fp_rate']:.0%}   漏报率 = {f['fn_rate']:.0%}")
    print(f"  审计事件 {f['audit_events']} 条（audit.log），其中规则命中 {f['audit_kinds'].get('rule_hit', 0)} 次:")
    for path, rule in f["rule_hits"]:
        print(f"    - {rule:24s} {path}")
    print(f"  合法更新准入: {f['audit_kinds'].get('admitted_update', 0)} 次")
    print(f"  仅权限变化降级为审计: {f['audit_kinds'].get('perms_changed', 0)} 次")
    print(f"  检出的篡改: {', '.join(f['detected_attacks'])}")
    print("\n运行目录(保留): " + r["workdir"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    r = run()
    if not args.keep:
        import shutil as _sh
        wd = r["workdir"]
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        print_report(r)
    if not args.keep:
        _sh.rmtree(wd, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
