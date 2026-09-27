"""噪声量化与修复验证仿真。

在同一棵目录树上、同一事件流下，分别跑"修复前(legacy)"与"修复后(fixed)"
两个监控器，按事件的真实标签（benign/malicious）统计：

- 误报 FP：对良性事件产生的告警（按来源分类：合法更新/临时文件/权限变化/
  日志轮转/原子重建/删除重建/touch）
- 漏报 FN：未被告警的恶意事件（重点：大小与 mtime 不变的隐蔽篡改）

用法：
    python3 -m fim.simulate [--cycles 300] [--seed 42] [--workdir out]

退出码：修复版出现任何 FP 或 FN 时为 1，否则为 0（可直接做回归门禁）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List

from .audit import AuditLog
from .authjournal import AuthorizationJournal
from .legacy import LegacyMonitor
from .monitor import IntegrityMonitor, sha256_file

BENIGN = "benign"
MALICIOUS = "malicious"


@dataclass
class Event:
    label: str
    category: str
    path: str  # 相对监控根


@dataclass
class Ctx:
    root: str
    journal: AuthorizationJournal
    rng: random.Random
    seq: int = 0

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def full(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def read(self, rel: str) -> bytes:
        with open(self.full(rel), "rb") as fh:
            return fh.read()

    def write_inplace(self, rel: str, data: bytes) -> None:
        with open(self.full(rel), "wb") as fh:
            fh.write(data)

    def write_atomic(self, rel: str, data: bytes) -> None:
        tmp = self.full(rel) + ".new"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, self.full(rel))


# ---------------- 场景 ----------------

def sc_legit_update(ctx: Ctx) -> List[Event]:
    """包管理器合法升级 usr/bin/tool（原子替换），已预登记授权。"""
    rel = "usr/bin/tool"
    new = ctx.read(rel) + b"\n# patched by pkg-mgr\n"
    ctx.journal.authorize(rel, hashlib.sha256(new).hexdigest(), actor="pkg-mgr")
    ctx.write_atomic(rel, new)
    return [Event(BENIGN, "legit_update", rel)]


def sc_temp_file(ctx: Ctx) -> List[Event]:
    """构建过程在 tmp/ 与 cache/ 下创建并删除临时文件。"""
    events: List[Event] = []
    for rel in ("tmp/obj-%d.o" % ctx.rng.randrange(10**6), "cache/cc-%d" % ctx.rng.randrange(10**6)):
        with open(ctx.full(rel), "wb") as fh:
            fh.write(os.urandom(64))
        events.append(Event(BENIGN, "temp_file", rel))
    for ev in events:
        os.remove(ctx.full(ev.path))
    return events


def sc_permission_change(ctx: Ctx) -> List[Event]:
    """运维收紧权限，内容不变。"""
    rel = "usr/bin/tool"
    cur = os.stat(ctx.full(rel)).st_mode & 0o777
    os.chmod(ctx.full(rel), 0o750 if cur != 0o750 else 0o755)
    return [Event(BENIGN, "permission_change", rel)]


def sc_log_rotation(ctx: Ctx) -> List[Event]:
    """日志轮转：app.log -> app.log.1，新建空 app.log。"""
    live = "var/log/app.log"
    rotated = f"var/log/app.log.{ctx.next_seq()}"
    os.rename(ctx.full(live), ctx.full(rotated))
    with open(ctx.full(live), "wb") as fh:
        fh.write(b"")
    return [
        Event(BENIGN, "log_rotation", live),
        Event(BENIGN, "log_rotation", rotated),
    ]


def sc_atomic_rewrite(ctx: Ctx) -> List[Event]:
    """配置管理工具原子重写配置，内容逐字节相同（inode/mtime 变化）。"""
    rel = "etc/app.conf"
    ctx.write_atomic(rel, ctx.read(rel))
    return [Event(BENIGN, "atomic_rewrite", rel)]


def sc_delete_recreate(ctx: Ctx) -> List[Event]:
    """重装同版本文件：删除后按原内容重建。"""
    rel = "opt/app/VERSION"
    data = ctx.read(rel)
    os.remove(ctx.full(rel))
    with open(ctx.full(rel), "wb") as fh:
        fh.write(data)
    return [Event(BENIGN, "delete_recreate", rel)]


def sc_touch_only(ctx: Ctx) -> List[Event]:
    """备份软件只更新 mtime。"""
    rel = "etc/app.conf"
    os.utime(ctx.full(rel), None)
    return [Event(BENIGN, "touch_only", rel)]


def sc_stealth_tamper(ctx: Ctx) -> List[Event]:
    """隐蔽篡改：等长替换内容，并恢复 mtime —— 元数据完全不变。"""
    rel = "usr/bin/tool"
    st = os.stat(ctx.full(rel))
    orig = ctx.read(rel)
    forged = bytearray(orig)
    marker = b"\x90" if orig[:1] != b"\x90" else b"\x91"
    forged[:8] = marker * 8  # 等长注入
    ctx.write_inplace(rel, bytes(forged))
    os.utime(ctx.full(rel), ns=(st.st_atime_ns, st.st_mtime_ns))  # 恢复时间戳
    return [Event(MALICIOUS, "stealth_tamper", rel)]


def sc_atomic_tamper(ctx: Ctx) -> List[Event]:
    """通过临时文件 + rename 的原子替换篡改配置（等长内容）。"""
    rel = "etc/app.conf"
    orig = ctx.read(rel)
    forged = orig.replace(b"mode=safe", b"mode=EVIL")
    if forged == orig:
        forged = orig[:-1] + (b"X" if orig[-1:] != b"X" else b"Y")
    ctx.write_atomic(rel, forged)
    return [Event(MALICIOUS, "atomic_tamper", rel)]


def sc_unauthorized_new_file(ctx: Ctx) -> List[Event]:
    """落地未授权新文件（如 webshell/恶意二进制）。"""
    rel = "usr/bin/svc-%d" % ctx.next_seq()
    with open(ctx.full(rel), "wb") as fh:
        fh.write(b"#!/bin/sh\n# backdoor\n")
    return [Event(MALICIOUS, "unauthorized_new_file", rel)]


def sc_delete_config(ctx: Ctx) -> List[Event]:
    """删除受监控文件（破坏可用性/掩盖痕迹）。"""
    rel = "etc/keys/backup-%d.key" % ctx.next_seq()
    os.makedirs(os.path.dirname(ctx.full(rel)), exist_ok=True)
    with open(ctx.full(rel), "wb") as fh:
        fh.write(os.urandom(32))
    # 先让两个监控器把它纳入基线，再删除（删除动作延后到预扫描之后）
    ctx.extra_prescan = True  # type: ignore[attr-defined]
    ctx.post_prescan = lambda: os.remove(ctx.full(rel))  # type: ignore[attr-defined]
    return [Event(MALICIOUS, "delete_config", rel)]


SCENARIOS: List[tuple] = [
    (sc_legit_update, 10),
    (sc_temp_file, 15),
    (sc_permission_change, 10),
    (sc_log_rotation, 15),
    (sc_atomic_rewrite, 8),
    (sc_delete_recreate, 8),
    (sc_touch_only, 10),
    (sc_stealth_tamper, 6),
    (sc_atomic_tamper, 6),
    (sc_unauthorized_new_file, 5),
    (sc_delete_config, 5),
]


def build_env(root: str) -> None:
    files = {
        "etc/app.conf": b"host=internal\nmode=safe\n",
        "usr/bin/tool": b"\x7fELF fake-binary-v1.2.3 " + b"A" * 64,
        "opt/app/VERSION": b"1.2.3\n",
        "var/lib/app/data.db": b"SQLITE-LIKE" + b"\x00" * 128,
        "var/log/app.log": b"boot ok\n",
    }
    for rel, data in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
    for d in ("tmp", "cache", "build", "etc/keys"):
        os.makedirs(os.path.join(root, d), exist_ok=True)


@dataclass
class Metrics:
    total_alerts: int = 0
    fp: int = 0
    tp: int = 0
    fn: int = 0
    malicious_events: int = 0
    fp_by_category: Dict[str, int] = field(default_factory=dict)
    fn_by_category: Dict[str, int] = field(default_factory=dict)

    @property
    def fpr(self) -> float:
        return self.fp / self.total_alerts if self.total_alerts else 0.0

    @property
    def fnr(self) -> float:
        return self.fn / self.malicious_events if self.malicious_events else 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "total_alerts": self.total_alerts,
            "false_positives": self.fp,
            "true_positives": self.tp,
            "false_negatives": self.fn,
            "malicious_events": self.malicious_events,
            "false_positive_rate": round(self.fpr, 4),
            "false_negative_rate": round(self.fnr, 4),
            "fp_by_category": dict(sorted(self.fp_by_category.items())),
            "fn_by_category": dict(sorted(self.fn_by_category.items())),
        }


def run(cycles: int, seed: int, workdir: str) -> Dict[str, object]:
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
    root = os.path.join(workdir, "root")
    os.makedirs(root)
    build_env(root)

    journal = AuthorizationJournal(os.path.join(workdir, "journal.jsonl"))
    audit = AuditLog(os.path.join(workdir, "audit_fixed.jsonl"))
    legacy = LegacyMonitor(root)
    fixed = IntegrityMonitor(root, audit=audit, auth=journal)
    legacy.baseline()
    fixed.baseline()

    rng = random.Random(seed)
    ctx = Ctx(root=root, journal=journal, rng=rng)
    metrics = {"legacy": Metrics(), "fixed": Metrics()}
    population = [fn for fn, _ in SCENARIOS]
    weights = [w for _, w in SCENARIOS]

    for _ in range(cycles):
        scenario = rng.choices(population, weights=weights, k=1)[0]
        ctx.extra_prescan = False  # type: ignore[attr-defined]
        ctx.post_prescan = None  # type: ignore[attr-defined]
        events = scenario(ctx)
        if getattr(ctx, "extra_prescan", False):
            legacy.scan()
            fixed.scan()
        if getattr(ctx, "post_prescan", None):
            ctx.post_prescan()  # type: ignore[attr-defined]
        by_path = {}
        for ev in events:
            by_path.setdefault(ev.path, ev)

        legacy_alerts = legacy.scan()
        fixed_alerts = fixed.scan()

        for name, alerts in (("legacy", legacy_alerts), ("fixed", fixed_alerts)):
            m = metrics[name]
            for al in alerts:
                m.total_alerts += 1
                ev = by_path.get(al.path)
                if ev is None or ev.label == BENIGN:
                    m.fp += 1
                    cat = ev.category if ev else "unexpected"
                    m.fp_by_category[cat] = m.fp_by_category.get(cat, 0) + 1
                else:
                    m.tp += 1
            alerted = {al.path for al in alerts}
            for ev in events:
                if ev.label == MALICIOUS:
                    m.malicious_events += 1
                    if ev.path not in alerted:
                        m.fn += 1
                        m.fn_by_category[ev.category] = m.fn_by_category.get(ev.category, 0) + 1

    audit.close()
    report = {
        "cycles": cycles,
        "seed": seed,
        "legacy": metrics["legacy"].as_dict(),
        "fixed": metrics["fixed"].as_dict(),
    }
    with open(os.path.join(workdir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return report


def _print(report: Dict[str, object]) -> None:
    def row(m: Dict[str, object]) -> str:
        return ("告警总数={total_alerts:<5} 误报FP={false_positives:<5} "
                "漏报FN={false_negatives:<4} 误报率={false_positive_rate:.1%} "
                "漏报率={false_negative_rate:.1%}").format(**m)  # type: ignore[arg-type]

    legacy, fixed = report["legacy"], report["fixed"]  # type: ignore[assignment]
    print(f"仿真: cycles={report['cycles']} seed={report['seed']}")
    print("-" * 78)
    print(f"[修复前 legacy] {row(legacy)}")
    print("  误报来源分布（噪声量化）:")
    for cat, n in legacy["fp_by_category"].items():  # type: ignore[index,union-attr]
        print(f"    {cat:<22} {n}")
    if legacy["fn_by_category"]:  # type: ignore[index]
        print("  漏报分布:")
        for cat, n in legacy["fn_by_category"].items():  # type: ignore[index,union-attr]
            print(f"    {cat:<22} {n}")
    print(f"[修复后 fixed ] {row(fixed)}")
    if fixed["fp_by_category"] or fixed["fn_by_category"]:  # type: ignore[index]
        print("  fp_by_category:", fixed["fp_by_category"])  # type: ignore[index]
        print("  fn_by_category:", fixed["fn_by_category"])  # type: ignore[index]
    print("-" * 78)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FIM 噪声量化与修复验证仿真")
    ap.add_argument("--cycles", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workdir", default="out")
    args = ap.parse_args(argv)

    report = run(args.cycles, args.seed, args.workdir)
    _print(report)
    print(f"报告: {os.path.join(args.workdir, 'report.json')}")
    print(f"审计日志(修复版): {os.path.join(args.workdir, 'audit_fixed.jsonl')}")

    fixed = report["fixed"]
    ok = fixed["false_positives"] == 0 and fixed["false_negatives"] == 0  # type: ignore[index]
    print("回归门禁:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
