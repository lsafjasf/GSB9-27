#!/usr/bin/env python3
"""漏跑巡检：从任务产出反推「没跑 / 跑了但产出为空 / 跑了且有产出」。

仅使用 Python 3 标准库。判定依据全部来自产出物（批次号、水位时间、
最后成功记录），不依赖进程存活状态。

子命令：
  inspect    巡检一份输入 JSON，输出带证据链的报告
  demo       内置手工构造的 4 个典型任务，直接生成报告与样例数据
  benchmark  批量构造场景（注入漏跑/卡死/补偿重复），输出检出率与误报率
  selftest   内置自测（单元级 + 端到端断言）
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import string
import unittest
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

NOT_RUN = "NOT_RUN"                 # 没跑（到点无产出、无成功记录）
RAN_EMPTY = "RAN_EMPTY"             # 跑了但产出为空
RAN_WITH_OUTPUT = "RAN_WITH_OUTPUT"  # 跑了且有产出
PAUSED = "PAUSED"                   # 人为暂停窗口内，不考核

MISSED_RUN = "MISSED_RUN"            # 漏跑（到点未产出）
STUCK = "STUCK"                      # 卡死（开始了但长时间未完成）
DUPLICATE_OUTPUT = "DUPLICATE_OUTPUT"  # 补偿执行导致重复产出
PAUSE_VIOLATION = "PAUSE_VIOLATION"  # 暂停窗口内却有产出
BATCH_GAP = "BATCH_GAP"              # 批次号断号（信息性证据）
WATERMARK_REGRESSION = "WATERMARK_REGRESSION"  # 水位倒退（信息性证据）

ALL_FINDINGS = {
    MISSED_RUN, STUCK, DUPLICATE_OUTPUT,
    PAUSE_VIOLATION, BATCH_GAP, WATERMARK_REGRESSION,
}


# ---------------------------------------------------------------------------
# 时间 / cron / 时区
# ---------------------------------------------------------------------------

def parse_dt(value: str) -> datetime:
    """解析 ISO-8601 时间；无时区按 UTC 处理；结尾 Z 兼容。"""
    if value is None:
        raise ValueError("时间字段为空")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cron_field(expr: str, minimum: int, maximum: int) -> set[int]:
    """展开单个 cron 字段（支持 * , - / 语法）。"""
    result: set[int] = set()
    for piece in expr.split(","):
        step = 1
        if "/" in piece:
            piece, step_text = piece.split("/", 1)
            step = int(step_text)
        if piece == "*" or piece == "":
            start, end = minimum, maximum
        elif "-" in piece:
            start_text, end_text = piece.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(piece)
        for value in range(start, end + 1, step):
            if not minimum <= value <= maximum:
                raise ValueError(f"cron 字段越界: {value} 不在 [{minimum},{maximum}]")
            result.add(value)
    return result


class CronSchedule:
    """五段 cron（分 时 日 月 周）。周字段 0/7 均为周日。"""

    def __init__(self, expr: str, tz_name: str):
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError(f"cron 必须是 5 段: {expr!r}")
        minute, hour, dom, month, dow = parts
        self.minutes = _cron_field(minute, 0, 59)
        self.hours = _cron_field(hour, 0, 23)
        self.days_of_month = _cron_field(dom, 1, 31)
        self.months = _cron_field(month, 1, 12)
        raw_dow = _cron_field(dow, 0, 7)
        self.weekdays = ({7} if 7 in raw_dow else set()) | (raw_dow - {7})
        self.dom_restricted = dom.strip() != "*"
        self.dow_restricted = dow.strip() != "*"
        self.tz = ZoneInfo(tz_name)

    def _day_matches(self, year: int, month: int, day: int) -> bool:
        if month not in self.months or day not in self.days_of_month:
            dom_ok = False
        else:
            dom_ok = True
        # weekday(): Monday=0 ... Sunday=6；cron: Sunday=0
        dow = (datetime(year, month, day).weekday() + 1) % 7
        dow_ok = dow in self.weekdays
        if self.dom_restricted and self.dow_restricted:
            return dom_ok or dow_ok  # Vixie cron：日/周都受限时取并集
        return dom_ok and dow_ok

    def iter_slots(self, start_utc: datetime, end_utc: datetime):
        """在 [start_utc, end_utc) 内产出计划触发时刻（UTC datetime）。

        按本地日历日枚举，正确处理：
        - DST 春季跳时（本地不存在的触发点被跳过，间隔表现为不固定）；
        - DST 秋季回拨（fold=1 消除歧义，不产生重复触发）；
        - 任意 IANA 时区（Asia/Shanghai 等无 DST 区域自然成立）。
        """
        day = start_utc.astimezone(self.tz).date()
        last_day = end_utc.astimezone(self.tz).date()
        emitted: set[datetime] = set()
        while day <= last_day:
            if self._day_matches(day.year, day.month, day.day):
                for hour in sorted(self.hours):
                    for minute in sorted(self.minutes):
                        naive_local = datetime(day.year, day.month, day.day,
                                               hour, minute)
                        local = naive_local.replace(tzinfo=self.tz, fold=0)
                        utc_time = local.astimezone(timezone.utc)
                        # 春季跳时：该本地墙钟时间不存在，跳过本次触发
                        if utc_time.astimezone(self.tz).replace(tzinfo=None) \
                                != naive_local:
                            continue
                        if start_utc <= utc_time < end_utc and utc_time not in emitted:
                            emitted.add(utc_time)
                            yield utc_time
            day += timedelta(days=1)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class Batch:
    job_id: str
    batch_no: int
    window_start: datetime
    window_end: datetime
    produced_at: datetime
    row_count: int
    watermark: datetime | None
    checksum: str = ""


@dataclass
class RunRecord:
    job_id: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    window_start: datetime | None = None
    window_end: datetime | None = None


@dataclass
class Finding:
    type: str
    job_id: str
    severity: str
    evidence: dict[str, Any]


@dataclass
class SlotVerdict:
    job_id: str
    slot_at: datetime
    state: str
    confidence: str
    evidence: dict[str, Any]


@dataclass
class JobConfig:
    job_id: str
    cron: str
    timezone: str
    expected_empty: bool
    grace_minutes: int
    max_run_minutes: int
    pause_windows: list[tuple[datetime, datetime]]


def normalize_job(raw: dict[str, Any]) -> JobConfig:
    tz_name = raw.get("timezone", "UTC")
    # 提前校验时区
    ZoneInfo(tz_name)
    pauses = []
    for win in raw.get("pause_windows", []):
        pauses.append((parse_dt(win["start"]), parse_dt(win["end"])))
    return JobConfig(
        job_id=raw["job_id"],
        cron=raw["cron"],
        timezone=tz_name,
        expected_empty=bool(raw.get("expected_empty", False)),
        grace_minutes=int(raw.get("grace_minutes", 60)),
        max_run_minutes=int(raw.get("max_run_minutes", 60)),
        pause_windows=pauses,
    )


def load_input(doc: dict[str, Any]):
    now = parse_dt(doc["as_of"])
    horizon_start = parse_dt(doc["horizon_start"])
    jobs = [normalize_job(j) for j in doc["jobs"]]
    batches: list[Batch] = []
    for raw in doc.get("batches", []):
        batches.append(Batch(
            job_id=raw["job_id"],
            batch_no=int(raw["batch_no"]),
            window_start=parse_dt(raw["window_start"]),
            window_end=parse_dt(raw["window_end"]),
            produced_at=parse_dt(raw["produced_at"]),
            row_count=int(raw.get("row_count", 0)),
            watermark=parse_dt(raw["watermark"]) if raw.get("watermark") else None,
            checksum=str(raw.get("checksum", "")),
        ))
    runs: list[RunRecord] = []
    for raw in doc.get("run_records", []):
        runs.append(RunRecord(
            job_id=raw["job_id"],
            started_at=parse_dt(raw["started_at"]),
            finished_at=parse_dt(raw["finished_at"]) if raw.get("finished_at") else None,
            status=raw.get("status", "unknown"),
            window_start=parse_dt(raw["window_start"]) if raw.get("window_start") else None,
            window_end=parse_dt(raw["window_end"]) if raw.get("window_end") else None,
        ))
    return now, horizon_start, jobs, batches, runs


# ---------------------------------------------------------------------------
# 巡检主逻辑
# ---------------------------------------------------------------------------

def _within_pause(slot, pauses):
    for start, end in pauses:
        if start <= slot < end:
            return start, end
    return None


def _expected_window_end(slot, slot_list, index):
    """某 slot 对应的数据窗口右端点：下一个计划触发时刻；最后一个取间隔估计。"""
    if index + 1 < len(slot_list):
        return slot_list[index + 1]
    if index > 0:
        return slot + (slot - slot_list[index - 1])
    return slot + timedelta(hours=1)


def inspect(doc):
    now, horizon_start, jobs, all_batches, all_runs = load_input(doc)

    findings = []
    verdicts = []
    job_summaries = []

    batches_by_job = {}
    for batch in all_batches:
        batches_by_job.setdefault(batch.job_id, []).append(batch)
    runs_by_job = {}
    for run in all_runs:
        runs_by_job.setdefault(run.job_id, []).append(run)

    for job in jobs:
        schedule = CronSchedule(job.cron, job.timezone)
        # 多取一天，保证 horizon 内最后一个 slot 能算出窗口右端点
        slot_list = sorted(schedule.iter_slots(
            horizon_start, now + timedelta(days=1)))

        # ---- 暂停窗口内是否有产出（暂停被违反） ----
        for batch in batches_by_job.get(job.job_id, []):
            # 以「产出动作发生时间」为准：窗口只是擦到暂停边界不算违规
            if _within_pause(batch.produced_at, job.pause_windows):
                findings.append(Finding(
                    PAUSE_VIOLATION, job.job_id, "warning",
                    {"batch_no": batch.batch_no,
                     "window_end": iso(batch.window_end),
                     "produced_at": iso(batch.produced_at),
                     "row_count": batch.row_count,
                     "basis": "产出动作发生在人为暂停区间内（批次窗口擦边不算）"}))

        # ---- 批次 -> slot 对齐：以数据窗口右端点为锚（对延迟/补偿免疫） ----
        used_batch_ids = set()
        assigned = {}
        ordered_batches = sorted(batches_by_job.get(job.job_id, []),
                                 key=lambda b: (b.window_end, b.produced_at))
        for idx, slot in enumerate(slot_list):
            if slot >= now + timedelta(days=1):
                continue
            expected_end = _expected_window_end(slot, slot_list, idx)
            for batch in ordered_batches:
                if id(batch) in used_batch_ids:
                    continue
                # 锚点是数据窗口右端点（=下一计划点），允许少量时钟/落库漂移；
                # 不用运行耗时级宽限，避免小时任务把相邻窗口的批次误配进来。
                anchor_delta = abs((batch.window_end - expected_end).total_seconds())
                if anchor_delta <= 600:
                    assigned.setdefault(idx, []).append(batch)
                    used_batch_ids.add(id(batch))

        # ---- 批次号断号 / 水位倒退（信息性证据链） ----
        seq = sorted(ordered_batches, key=lambda b: b.batch_no)
        prev_no = None
        for batch in seq:
            if prev_no is not None and batch.batch_no != prev_no + 1:
                in_pause = _within_pause(batch.window_end, job.pause_windows)
                findings.append(Finding(
                    BATCH_GAP, job.job_id,
                    "info" if in_pause else "warning",
                    {"batch_no": batch.batch_no, "prev_batch_no": prev_no,
                     "window_end": iso(batch.window_end),
                     "paused": bool(in_pause),
                     "basis": "批次号不连续（暂停期间可解释，否则需人工确认漏批）"}))
            prev_no = batch.batch_no
        # 水位倒退按「产出顺序」比较（补数批次号大但时间早不应误报）
        prod_seq = sorted(ordered_batches, key=lambda b: b.produced_at)
        prev_wm = None
        for batch in prod_seq:
            if prev_wm is not None and batch.watermark is not None and \
                    batch.watermark < prev_wm - timedelta(seconds=60):
                findings.append(Finding(
                    WATERMARK_REGRESSION, job.job_id, "warning",
                    {"batch_no": batch.batch_no,
                     "produced_at": iso(batch.produced_at),
                     "watermark": iso(batch.watermark),
                     "prev_watermark": iso(prev_wm),
                     "basis": "后产出的批次水位早于前一批次，疑似重复/补数"}))
            if batch.watermark is not None:
                prev_wm = batch.watermark

        # ---- 重复产出：同一窗口多批 / 同校验和 / 水位重复 ----
        for idx, group in assigned.items():
            if len(group) < 2:
                continue
            checksums = {b.checksum for b in group if b.checksum}
            watermarks = {b.watermark for b in group if b.watermark}
            dup_kind = ["same_window_multiple_batches"]
            if len(checksums) == 1:
                dup_kind.append("identical_checksum")
            if len(watermarks) == 1:
                dup_kind.append("identical_watermark")
            findings.append(Finding(
                DUPLICATE_OUTPUT, job.job_id, "critical",
                {"slot": iso(slot_list[idx]),
                 "batch_nos": sorted(b.batch_no for b in group),
                 "produced_at": sorted(iso(b.produced_at) for b in group),
                 "row_counts": sorted(b.row_count for b in group),
                 "signals": dup_kind,
                 "basis": "同一计划窗口出现多份产出，典型于补偿执行重放"}))

        # ---- 执行记录 -> slot（最近邻、窗口匹配），用于佐证「跑过」 ----
        runs = sorted(runs_by_job.get(job.job_id, []), key=lambda r: r.started_at)
        run_map = {}
        for run in runs:
            best_idx = None
            best_delta = None
            for idx, slot in enumerate(slot_list):
                if slot > now:
                    break
                if run.window_end is not None:
                    expected_end = _expected_window_end(slot, slot_list, idx)
                    delta = abs((run.window_end - expected_end).total_seconds())
                else:
                    delta = abs((run.started_at - slot).total_seconds())
                if best_delta is None or delta < best_delta:
                    best_delta, best_idx = delta, idx
            if best_idx is not None and best_delta is not None and \
                    best_delta <= timedelta(hours=6).total_seconds():
                run_map.setdefault(best_idx, []).append(run)

        # ---- 逐 slot 三态判定 ----
        due_slots = 0
        state_counts = {NOT_RUN: 0, RAN_EMPTY: 0, RAN_WITH_OUTPUT: 0, PAUSED: 0}
        not_run_cluster = []
        prev_due_idx = None

        def flush_not_run(cluster):
            if not cluster:
                return
            # 仅把时间上相邻的 NOT_RUN 合并为一段
            run = [cluster[0]]

            def emit(piece):
                indices = [i for i, _ in piece]
                first, last = slot_list[indices[0]], slot_list[indices[-1]]
                findings.append(Finding(
                    MISSED_RUN, job.job_id, "critical",
                    {"slots": [iso(slot_list[i]) for i in indices],
                     "count": len(indices),
                     "first_slot": iso(first), "last_slot": iso(last),
                     "evidence_types": sorted({
                         sig
                         for _, verdict in piece
                         for sig in verdict.evidence.get("missing_evidence", [])}),
                     "basis": ("到点后超过宽限期仍无批次产出；"
                               "无对应窗口的成功执行记录；水位未推进")}))

            for item in cluster[1:]:
                if item[0] == run[-1][0] + 1:
                    run.append(item)
                else:
                    emit(run)
                    run = [item]
            emit(run)

        for idx, slot in enumerate(slot_list):
            pause = _within_pause(slot, job.pause_windows)
            due_at = slot + timedelta(minutes=job.grace_minutes)
            if pause is not None:
                verdicts.append(SlotVerdict(
                    job.job_id, slot, PAUSED, "high",
                    {"pause_window": [iso(pause[0]), iso(pause[1])],
                     "basis": "计划点位于人为暂停窗口，不参与漏跑考核"}))
                state_counts[PAUSED] += 1
                continue
            if due_at > now:
                continue  # 还没到宽限判定时刻
            due_slots += 1
            groups = assigned.get(idx, [])
            slot_runs = run_map.get(idx, [])
            prev_due_idx = idx

            if groups:
                rows = sum(b.row_count for b in groups)
                latest_wm = max((b.watermark for b in groups if b.watermark),
                                default=None)
                empty = all(b.row_count == 0 for b in groups)
                state = RAN_EMPTY if empty else RAN_WITH_OUTPUT
                evidence = {
                    "batch_nos": [b.batch_no for b in groups],
                    "produced_at": [iso(b.produced_at) for b in groups],
                    "row_count": rows,
                    "watermark": iso(latest_wm) if latest_wm else None,
                    "latency_seconds": [
                        int((b.produced_at - slot).total_seconds())
                        for b in groups],
                    "duplicate_batches": len(groups) > 1,
                    "basis": "存在归属本窗口的批次产出（按窗口右端点对齐）",
                }
                state_counts[state] += 1
                verdicts.append(SlotVerdict(job.job_id, slot, state, "high", evidence))
                flush_not_run(not_run_cluster)
                not_run_cluster = []
                continue

            # 无批次产出：用执行记录区分「没跑」与「跑了但空」
            success_runs = [r for r in slot_runs if r.status == "success"]
            stale_running = [r for r in slot_runs if r.status == "running"
                             and r.finished_at is None
                             and now - r.started_at >
                             timedelta(minutes=job.max_run_minutes)]
            if success_runs:
                run = success_runs[0]
                verdicts.append(SlotVerdict(
                    job.job_id, slot, RAN_EMPTY, "medium",
                    {"run_started_at": iso(run.started_at),
                     "run_finished_at": iso(run.finished_at)
                     if run.finished_at else None,
                     "basis": ("无批次产出但存在 status=success 的执行记录，"
                               "判定为跑了但产出为空（0 行不写批次表）")}))
                state_counts[RAN_EMPTY] += 1
                flush_not_run(not_run_cluster)
                not_run_cluster = []
                continue

            missing_evidence = ["no_batch_output",
                                "no_success_run_record",
                                "watermark_not_advanced"]
            if stale_running:
                missing_evidence.append("stale_running_record")
            verdict = SlotVerdict(
                job.job_id, slot, NOT_RUN, "high",
                {"missing_evidence": missing_evidence,
                 "started_records": len(slot_runs),
                 "stale_running": bool(stale_running),
                 "grace_deadline": iso(due_at),
                 "basis": ("超过宽限期无批次、无成功执行记录，且水位未推进"
                           "（基于产出反推，不看进程存活）")})
            state_counts[NOT_RUN] += 1
            verdicts.append(verdict)
            not_run_cluster.append((idx, verdict))

        flush_not_run(not_run_cluster)

        # ---- 卡死：有 running 且超时未完成（进程可能还活着，但产出可证） ----
        for run in runs:
            if run.status == "running" and run.finished_at is None and \
                    now - run.started_at > timedelta(minutes=job.max_run_minutes):
                findings.append(Finding(
                    STUCK, job.job_id, "critical",
                    {"started_at": iso(run.started_at),
                     "stuck_minutes": int((now - run.started_at).total_seconds() / 60),
                     "max_run_minutes": job.max_run_minutes,
                     "window_end": iso(run.window_end) if run.window_end else None,
                     "basis": ("执行记录停留在 running 超过最大时长，"
                               "其后计划窗口均无产出/无成功记录")}))

        # ---- 暂停窗口内存在成功执行（暂停被违反，反向校验） ----
        for run in runs:
            pause = _within_pause(run.started_at, job.pause_windows)
            if pause and run.status == "success":
                findings.append(Finding(
                    PAUSE_VIOLATION, job.job_id, "warning",
                    {"started_at": iso(run.started_at),
                     "pause_window": [iso(pause[0]), iso(pause[1])],
                     "basis": "人为暂停窗口内存在成功执行记录"}))

        job_summaries.append({
            "job_id": job.job_id,
            "cron": job.cron,
            "timezone": job.timezone,
            "due_slots": due_slots,
            "states": state_counts,
        })

    return {
        "as_of": iso(now),
        "horizon_start": iso(horizon_start),
        "jobs": job_summaries,
        "slot_verdicts": [
            {"job_id": v.job_id, "slot_at": iso(v.slot_at),
             "state": v.state, "confidence": v.confidence,
             "evidence": v.evidence}
            for v in verdicts],
        "findings": [
            {"type": f.type, "job_id": f.job_id,
             "severity": f.severity, "evidence": f.evidence}
            for f in findings],
        "state_totals": _state_totals(verdicts),
        "finding_totals": {
            kind: sum(1 for f in findings if f.type == kind)
            for kind in sorted(ALL_FINDINGS)},
    }


def _state_totals(verdicts):
    totals = {NOT_RUN: 0, RAN_EMPTY: 0, RAN_WITH_OUTPUT: 0, PAUSED: 0}
    for verdict in verdicts:
        totals[verdict.state] += 1
    return totals


# ---------------------------------------------------------------------------
# 报告渲染（纯文本，带证据链）
# ---------------------------------------------------------------------------

STATE_CN = {
    NOT_RUN: "没跑",
    RAN_EMPTY: "跑了但产出为空",
    RAN_WITH_OUTPUT: "跑了且有产出",
    PAUSED: "人为暂停",
}

FINDING_CN = {
    MISSED_RUN: "漏跑",
    STUCK: "卡死",
    DUPLICATE_OUTPUT: "补偿重复产出",
    PAUSE_VIOLATION: "暂停窗口违规",
    BATCH_GAP: "批次号断号",
    WATERMARK_REGRESSION: "水位倒退",
}


def _fmt_evidence(evidence, indent=6):
    lines = []
    pad = " " * indent
    for key, value in evidence.items():
        if key == "basis":
            continue
        lines.append(f"{pad}- {key}: {json.dumps(value, ensure_ascii=False)}")
    basis = evidence.get("basis")
    if basis:
        lines.append(f"{pad}- 判定依据: {basis}")
    return "\n".join(lines)


def render_report(result):
    out = io.StringIO()
    w = out.write
    w("=" * 78 + "\n")
    w("定时任务漏跑巡检报告（基于产出反推，不依赖进程存活）\n")
    w("=" * 78 + "\n")
    w(f"巡检基准时间 as_of : {result['as_of']}\n")
    w(f"巡检窗口 horizon   : {result['horizon_start']} ~ {result['as_of']}\n\n")

    totals = result["state_totals"]
    w("一、计划窗口三态汇总\n")
    w("-" * 78 + "\n")
    for state in (RAN_WITH_OUTPUT, RAN_EMPTY, NOT_RUN, PAUSED):
        w(f"  {STATE_CN[state]:<10} ({state:<16}): {totals[state]}\n")
    w("\n二、异常发现汇总\n")
    w("-" * 78 + "\n")
    ftotals = result["finding_totals"]
    for kind in (MISSED_RUN, STUCK, DUPLICATE_OUTPUT, PAUSE_VIOLATION,
                 BATCH_GAP, WATERMARK_REGRESSION):
        w(f"  {FINDING_CN[kind]:<12} ({kind:<18}): {ftotals[kind]}\n")

    w("\n三、任务级明细\n")
    w("-" * 78 + "\n")
    for job in result["jobs"]:
        st = job["states"]
        w(f"  [{job['job_id']}] cron='{job['cron']}' tz={job['timezone']} "
          f"到期窗口={job['due_slots']}  "
          f"有产出={st[RAN_WITH_OUTPUT]} 空产出={st[RAN_EMPTY]} "
          f"没跑={st[NOT_RUN]} 暂停={st[PAUSED]}\n")

    w("\n四、异常证据链\n")
    w("-" * 78 + "\n")
    if not result["findings"]:
        w("  无异常发现。\n")
    for i, finding in enumerate(result["findings"], 1):
        w(f"  [{i}] [{finding['severity'].upper()}] "
          f"{FINDING_CN[finding['type']]} / {finding['type']} "
          f"job={finding['job_id']}\n")
        w(_fmt_evidence(finding["evidence"]) + "\n")

    w("\n五、逐窗口判定台账（按任务/时间排序）\n")
    w("-" * 78 + "\n")
    w(f"  {'计划时间(UTC)':<22}{'任务':<14}{'状态':<18}{'置信度':<8}证据\n")
    for verdict in result["slot_verdicts"]:
        ev = verdict["evidence"]
        if verdict["state"] == RAN_WITH_OUTPUT:
            detail = (f"batch={ev.get('batch_nos')} rows={ev.get('row_count')} "
                      f"wm={ev.get('watermark')}")
        elif verdict["state"] == RAN_EMPTY:
            detail = ev.get("run_finished_at") or "0行批次"
        elif verdict["state"] == NOT_RUN:
            detail = ",".join(ev.get("missing_evidence", []))
        else:
            detail = "暂停窗口内"
        w(f"  {verdict['slot_at']:<22}{verdict['job_id']:<14}"
          f"{verdict['state']:<18}{verdict['confidence']:<8}{detail}\n")
    w("=" * 78 + "\n")
    return out.getvalue()


# ---------------------------------------------------------------------------
# 场景构造（产出模拟器）：批次号 / 水位 / 执行记录三要素
# ---------------------------------------------------------------------------

def _checksum(job_id, slot, tag=""):
    raw = f"{job_id}|{iso(slot)}|{tag}"
    return format(abs(hash(raw)) & 0xFFFFFFFF, "08x")


def _make_batch_dict(job_id, batch_no, slot, window_end, produced_at,
                     row_count, watermark, tag=""):
    return {
        "job_id": job_id,
        "batch_no": batch_no,
        "window_start": iso(slot),
        "window_end": iso(window_end),
        "produced_at": iso(produced_at),
        "row_count": row_count,
        "watermark": iso(watermark) if watermark else None,
        "checksum": _checksum(job_id, slot, tag),
    }


def build_demo():
    """手工构造的 4 个典型任务，覆盖漏跑/空产出/DST/暂停/卡死/补偿重复。"""
    as_of = parse_dt("2026-03-10T13:00:00Z")
    horizon = parse_dt("2026-03-01T00:00:00Z")
    jobs = [
        {"job_id": "billing_hourly_sh", "cron": "30 * * * *",
         "timezone": "Asia/Shanghai", "grace_minutes": 20,
         "max_run_minutes": 25},
        {"job_id": "settle_daily_ny", "cron": "5 2 * * *",
         "timezone": "America/New_York", "grace_minutes": 60,
         "max_run_minutes": 45},
        {"job_id": "etx_6h_utc", "cron": "15 */6 * * *",
         "timezone": "UTC", "grace_minutes": 30,
         "max_run_minutes": 40,
         "pause_windows": [
             {"start": "2026-03-04T06:00:00Z",
              "end": "2026-03-04T18:30:00Z"}]},
        {"job_id": "report_weekday", "cron": "20 9 * * 1-5",
         "timezone": "Asia/Shanghai", "grace_minutes": 30,
         "max_run_minutes": 30},
    ]
    batches = []
    runs = []
    truth = []  # (job_id, slot_iso, true_state, scenario_note)

    def job_slots(cfg):
        sched = CronSchedule(cfg["cron"], cfg["timezone"])
        pauses = [(parse_dt(w["start"]), parse_dt(w["end"]))
                  for w in cfg.get("pause_windows", [])]
        slots = []
        for slot in sched.iter_slots(horizon, as_of + timedelta(days=1)):
            if slot + timedelta(minutes=cfg["grace_minutes"]) > as_of:
                continue
            slots.append((slot, _within_pause(slot, pauses)))
        return slots, pauses

    def emit_success(job_id, idx, slot, next_slot, batch_no, empty=False,
                     late_minutes=0, tag="", rows=120):
        started = slot + timedelta(minutes=1 + late_minutes)
        duration = 6 + (idx % 5) + late_minutes
        produced = started + timedelta(minutes=duration)
        finished = produced
        wm = None if empty else (next_slot - timedelta(seconds=1))
        runs.append({"job_id": job_id, "started_at": iso(started),
                     "finished_at": iso(finished), "status": "success",
                     "window_start": iso(slot), "window_end": iso(next_slot)})
        if empty:
            return None
        batch = _make_batch_dict(job_id, batch_no, slot, next_slot, produced,
                                 rows, wm, tag)
        batches.append(batch)
        return batch

    # --- J1: 每小时（上海，无 DST）。注入 1 次漏跑 + 1 次真空产出 ---------
    cfg = jobs[0]
    slots, _ = job_slots(cfg)
    missed_j1 = slots[50][0]
    empty_j1 = slots[77][0]
    batch_no = 1000
    for idx, (slot, _) in enumerate(slots):
        next_slot = slots[idx + 1][0] if idx + 1 < len(slots) else slot + timedelta(hours=1)
        if slot == missed_j1:
            truth.append((cfg["job_id"], iso(slot), NOT_RUN, "missed", "漏跑：调度静默失败，进程仍存活"))
            continue
        if slot == empty_j1:
            emit_success(cfg["job_id"], idx, slot, next_slot, batch_no, empty=True)
            truth.append((cfg["job_id"], iso(slot), RAN_EMPTY, "missed", "空产出：0 行不写批次表"))
            continue
        emit_success(cfg["job_id"], idx, slot, next_slot, batch_no)
        batch_no += 1
        truth.append((cfg["job_id"], iso(slot), RAN_WITH_OUTPUT, "missed", "正常"))

    # --- J2: 每日（纽约）。3/8 DST 春季跳时 02:05 本地时间不存在 -----------
    cfg = jobs[1]
    slots, _ = job_slots(cfg)
    batch_no = 200
    for idx, (slot, _) in enumerate(slots):
        next_slot = slots[idx + 1][0] if idx + 1 < len(slots) else slot + timedelta(days=1)
        emit_success(cfg["job_id"], idx, slot, next_slot, batch_no)
        batch_no += 1
        note = "正常"
        if idx + 1 < len(slots) and slots[idx + 1][0] - slot != timedelta(days=1):
            note = "DST 前一日（次日本地触发点不存在，间隔非 24h 属预期）"
        if idx > 0 and slot - slots[idx - 1][0] != timedelta(days=1):
            note = "DST 后一日（间隔非 24h 属预期，非漏跑）"
        truth.append((cfg["job_id"], iso(slot), RAN_WITH_OUTPUT, "dst", note))

    # --- J3: 每 6 小时（UTC）。暂停+暂停违规、卡死、补偿重复 ---------------
    cfg = jobs[2]
    slots, _ = job_slots(cfg)
    stuck_start = parse_dt("2026-03-08T18:15:00Z")
    dup_slot = parse_dt("2026-03-03T00:15:00Z")
    batch_no = 1
    for idx, (slot, paused) in enumerate(slots):
        next_slot = slots[idx + 1][0] if idx + 1 < len(slots) else slot + timedelta(hours=6)
        if paused:
            truth.append((cfg["job_id"], iso(slot), PAUSED, "pause_stuck_dup", "人为暂停窗口内"))
            continue
        if slot == stuck_start:
            runs.append({"job_id": cfg["job_id"],
                         "started_at": iso(slot + timedelta(minutes=2)),
                         "finished_at": None, "status": "running",
                         "window_start": iso(slot), "window_end": iso(next_slot)})
            truth.append((cfg["job_id"], iso(slot), NOT_RUN, "pause_stuck_dup", "卡死：running 超过 40 分钟无产出"))
            continue
        if slot > stuck_start:
            truth.append((cfg["job_id"], iso(slot), NOT_RUN, "pause_stuck_dup", "卡死后的连锁漏跑"))
            continue
        b = emit_success(cfg["job_id"], idx, slot, next_slot, batch_no)
        if b:
            batch_no += 1
        if slot == dup_slot:
            # 补偿执行：隔 2 小时重放同一窗口，批次号不同、水位/窗口相同
            replay_at = slot + timedelta(hours=2, minutes=12)
            batches.append(_make_batch_dict(
                cfg["job_id"], 900 + idx, slot, next_slot, replay_at,
                b["row_count"], parse_dt(b["watermark"]), "replay"))
            truth.append((cfg["job_id"], iso(slot), RAN_WITH_OUTPUT, "pause_stuck_dup", "补偿执行导致同窗口重复产出"))
        else:
            truth.append((cfg["job_id"], iso(slot), RAN_WITH_OUTPUT, "pause_stuck_dup", "正常"))
    # 暂停窗口内人为手动跑了一次（违规证据）
    runs.append({"job_id": cfg["job_id"],
                 "started_at": "2026-03-04T09:05:00Z",
                 "finished_at": "2026-03-04T09:11:00Z",
                 "status": "success", "window_start": None, "window_end": None})
    batches.append({
        "job_id": cfg["job_id"], "batch_no": 800,
        "window_start": "2026-03-04T09:00:00Z",
        "window_end": "2026-03-04T09:12:00Z",
        "produced_at": "2026-03-04T09:11:00Z",
        "row_count": 53, "watermark": "2026-03-04T09:11:50Z",
        "checksum": "pause-violation"})

    # --- J4: 工作日固定时刻（上海）。1 次真空产出 --------------------------
    cfg = jobs[3]
    slots, _ = job_slots(cfg)
    empty_j4 = slots[2][0]
    batch_no = 50
    for idx, (slot, _) in enumerate(slots):
        next_slot = slots[idx + 1][0] if idx + 1 < len(slots) else slot + timedelta(days=1)
        empty = slot == empty_j4
        emit_success(cfg["job_id"], idx, slot, next_slot, batch_no, empty=empty)
        if not empty:
            batch_no += 1
        state = RAN_EMPTY if empty else RAN_WITH_OUTPUT
        truth.append((cfg["job_id"], iso(slot), state, "irregular",
                      "空产出：0 行不写批次表" if empty else "正常（仅工作日触发）"))

    doc = {"as_of": iso(as_of), "horizon_start": iso(horizon),
           "jobs": jobs, "batches": batches, "run_records": runs}
    return doc, truth


# ---------------------------------------------------------------------------
# 量化 benchmark：批量构造，注入漏跑/卡死/补偿重复/暂停，统计检出率/误报率
# ---------------------------------------------------------------------------

CATALOG = [
    # cron, 时区, 宽限(分), 最大耗时(分) —— 覆盖不固定间隔与时区变化
    ("5 * * * *", "UTC", 20, 25),
    ("30 * * * *", "Asia/Shanghai", 20, 25),
    ("7 2 * * *", "America/New_York", 75, 50),
    ("13 3 * * *", "Europe/Berlin", 75, 50),
    ("23 */6 * * *", "UTC", 30, 40),
    ("11 1 * * *", "Asia/Shanghai", 60, 45),
    ("47 9,21 * * *", "America/Los_Angeles", 60, 40),
    ("19 4 * * 1-5", "Europe/London", 70, 45),
    ("3 12 * * *", "Australia/Sydney", 70, 45),
    ("0 0 * * *", "UTC", 45, 30),
]


def build_benchmark(n_jobs=160, seed=20260310):
    """返回 (input_doc, truth_rows)。

    场景占比：漏跑/卡死/补偿重复/人为暂停 各 10%，其余 60% 为干净任务
    （其中部分任务空产出占比更高）。另注入少量自然失败、0 行、晚到、
    重试成功，模拟真实噪声。
    """
    rng = random.Random(seed)
    as_of = parse_dt("2026-04-01T12:00:00Z")
    horizon = parse_dt("2026-03-02T00:00:00Z")
    jobs_cfg = []
    batches = []
    runs = []
    truth = []

    n_bad = n_jobs // 10  # 每类 10%
    scenarios = (
        ["missed"] * n_bad + ["stuck"] * n_bad +
        ["duplicate"] * n_bad + ["paused"] * n_bad)
    scenarios += ["clean"] * (n_jobs - len(scenarios))
    rng.shuffle(scenarios)

    for j in range(n_jobs):
        scenario = scenarios[j]
        cron, tz_name, grace, max_run = CATALOG[j % len(CATALOG)]
        job_id = f"job_{j:03d}_{scenario}"
        cfg = {"job_id": job_id, "cron": cron, "timezone": tz_name,
               "grace_minutes": grace, "max_run_minutes": max_run,
               "pause_windows": []}
        sched = CronSchedule(cron, tz_name)
        raw_slots = sorted(sched.iter_slots(horizon, as_of + timedelta(days=1)))

        pause_windows = []
        if scenario == "paused" and len(raw_slots) > 8:
            ps = raw_slots[4]
            pe = ps + timedelta(hours=30)
            pause_windows = [(ps, pe)]
            cfg["pause_windows"] = [
                {"start": iso(ps), "end": iso(pe)}]

        slots = []
        for slot in raw_slots:
            if slot + timedelta(minutes=grace) > as_of:
                continue
            paused = _within_pause(slot, pause_windows) is not None
            slots.append((slot, paused))

        jobs_cfg.append(cfg)
        elig = [k for k, (_, paused) in enumerate(slots) if not paused]

        missed_indices = set()
        stuck_idx = None
        duplicate_indices = set()
        empty_heavy = scenario == "clean" and (j % 5 == 0)
        if scenario == "missed" and len(elig) > 6:
            for _ in range(2):
                pick = rng.choice(elig[2:-3])
                if all(abs(pick - m) > 1 for m in missed_indices):
                    missed_indices.add(pick)
        if scenario == "stuck" and len(elig) > 6:
            stuck_idx = elig[rng.randint(3, len(elig) - 4)]
        if scenario == "duplicate" and len(elig) > 6:
            duplicate_indices = {rng.choice(elig[2:-3]),
                                 rng.choice(elig[2:-3])}

        batch_no = 1
        for idx, (slot, paused) in enumerate(slots):
            next_slot = (slots[idx + 1][0] if idx + 1 < len(slots)
                         else slot + (slots[1][0] - slots[0][0]))
            if paused:
                truth.append([job_id, iso(slot), PAUSED, scenario, "人为暂停"])
                continue
            if scenario == "stuck" and idx >= stuck_idx:
                if idx == stuck_idx:
                    runs.append({"job_id": job_id,
                                 "started_at": iso(slot + timedelta(minutes=1)),
                                 "finished_at": None, "status": "running",
                                 "window_start": iso(slot),
                                 "window_end": iso(next_slot)})
                    truth.append([job_id, iso(slot), NOT_RUN, scenario,
                                  "卡死：running 超时未完成"])
                else:
                    truth.append([job_id, iso(slot), NOT_RUN, scenario,
                                  "卡死后连锁漏跑"])
                continue
            if idx in missed_indices:
                truth.append([job_id, iso(slot), NOT_RUN, scenario, "注入漏跑"])
                continue
            # 自然噪声：干净/暂停任务偶发执行失败（真 NOT_RUN）
            natural_fail = (scenario in ("clean", "paused")
                            and rng.random() < 0.01)
            if natural_fail:
                runs.append({"job_id": job_id,
                             "started_at": iso(slot + timedelta(minutes=1)),
                             "finished_at": iso(slot + timedelta(minutes=4)),
                             "status": "failed", "window_start": iso(slot),
                             "window_end": iso(next_slot)})
                truth.append([job_id, iso(slot), NOT_RUN, scenario, "自然失败"])
                continue
            empty_prob = 0.08 if empty_heavy else 0.003
            is_empty = rng.random() < empty_prob
            late = 0
            if rng.random() < 0.12:
                late = rng.randint(3, max(3, (max_run + grace) // 3))
            started = slot + timedelta(minutes=1 + late)
            produced = started + timedelta(minutes=5 + (idx % 7) + late // 2)
            runs.append({"job_id": job_id, "started_at": iso(started),
                         "finished_at": iso(produced), "status": "success",
                         "window_start": iso(slot), "window_end": iso(next_slot)})
            note = "晚到但成功" if late else "正常"
            if is_empty:
                truth.append([job_id, iso(slot), RAN_EMPTY, scenario,
                              "空产出（0 行）"])
                continue
            wm = next_slot - timedelta(seconds=rng.randint(1, 59))
            batches.append(_make_batch_dict(
                job_id, batch_no, slot, next_slot, produced,
                rng.randint(50, 5000), wm))
            batch_no += 1
            if idx in duplicate_indices:
                replay_at = slot + timedelta(hours=2, minutes=rng.randint(5, 50))
                batches.append(_make_batch_dict(
                    job_id, 90000 + idx, slot, next_slot, replay_at,
                    rng.randint(50, 5000), wm, "replay"))
                note = "注入补偿重复产出"
            truth.append([job_id, iso(slot), RAN_WITH_OUTPUT, scenario, note])

    doc = {"as_of": iso(as_of), "horizon_start": iso(horizon),
           "jobs": jobs_cfg, "batches": batches, "run_records": runs}
    return doc, truth


# ---------------------------------------------------------------------------
# 指标：检出率 / 误报率
# ---------------------------------------------------------------------------

def score(result, truth_rows):
    """按 slot 三态混淆矩阵 + 事件级（漏跑段/卡死/重复）检出与误报打分。"""
    pred = {}
    for verdict in result["slot_verdicts"]:
        pred[(verdict["job_id"], verdict["slot_at"])] = verdict["state"]

    labels = [NOT_RUN, RAN_EMPTY, RAN_WITH_OUTPUT]
    matrix = {a: {b: 0 for b in labels} for a in labels}
    for job_id, slot_iso, true_state, _scenario, _note in truth_rows:
        if true_state == PAUSED:
            continue
        guessed = pred.get((job_id, slot_iso))
        if guessed in labels:
            matrix[true_state][guessed] += 1

    def recall(state):
        total = sum(matrix[state].values())
        return matrix[state][state] / total if total else None

    def precision(state):
        total = sum(matrix[a][state] for a in labels)
        return matrix[state][state] / total if total else None

    def fpr(state):
        neg = sum(matrix[a][b] for a in labels if a != state
                  for b in labels)
        fp = sum(matrix[a][state] for a in labels if a != state)
        return fp / neg if neg else None

    truth_by_job = {}
    for job_id, slot_iso, state, scenario, note in truth_rows:
        truth_by_job.setdefault(job_id, []).append(
            (parse_dt(slot_iso), state, scenario, note))

    findings_by_job = {}
    for finding in result["findings"]:
        findings_by_job.setdefault(finding["job_id"], []).append(finding)

    # 事件级：漏跑段（相邻 NOT_RUN 真值）、卡死、补偿重复
    event_stats = {"missed_groups": {"detected": 0, "total": 0, "false_alarms": 0},
                   "stuck": {"detected": 0, "total": 0, "false_alarms": 0},
                   "duplicate": {"detected": 0, "total": 0, "false_alarms": 0}}

    all_jobs = set(truth_by_job) | set(findings_by_job)
    for job_id in sorted(all_jobs):
        rows = truth_by_job.get(job_id, [])
        flist = findings_by_job.get(job_id, [])
        slots_utc = [r[0] for r in rows]
        slot_index = {s: i for i, s in enumerate(slots_utc)}

        # 漏跑段真值（同场景内相邻 NOT_RUN 归一段）
        missed_slots = sorted(s for s, state, sc, note in rows
                              if state == NOT_RUN and "卡死" not in note
                              and "连锁" not in note)
        true_groups = []
        run = []
        for slot in missed_slots:
            if run and slot_index[slot] == slot_index[run[-1]] + 1:
                run.append(slot)
            else:
                if run:
                    true_groups.append(run)
                run = [slot]
        if run:
            true_groups.append(run)

        pred_missed_slots = set()
        for finding in flist:
            if finding["type"] == MISSED_RUN:
                for slot_text in finding["evidence"]["slots"]:
                    pred_missed_slots.add(parse_dt(slot_text))
        for group in true_groups:
            event_stats["missed_groups"]["total"] += 1
            if any(s in pred_missed_slots for s in group):
                event_stats["missed_groups"]["detected"] += 1
        # 误报：预测漏跑但该 slot 真值不是 NOT_RUN
        for slot in pred_missed_slots:
            i = slot_index.get(slot)
            if i is None or rows[i][1] != NOT_RUN:
                event_stats["missed_groups"]["false_alarms"] += 1

        is_stuck = any("卡死" in note for _, _, _, note in rows)
        pred_stuck = [f for f in flist if f["type"] == STUCK]
        if is_stuck:
            event_stats["stuck"]["total"] += 1
            if pred_stuck:
                event_stats["stuck"]["detected"] += 1
        elif pred_stuck:
            event_stats["stuck"]["false_alarms"] += len(pred_stuck)

        dup_slots = sorted(s for s, state, sc, note in rows
                           if "重复" in note)
        pred_dup = [f for f in flist if f["type"] == DUPLICATE_OUTPUT]
        pred_dup_slots = set()
        for finding in pred_dup:
            pred_dup_slots.add(parse_dt(finding["evidence"]["slot"]))
        for slot in dup_slots:
            event_stats["duplicate"]["total"] += 1
            if slot in pred_dup_slots:
                event_stats["duplicate"]["detected"] += 1
        for slot in pred_dup_slots:
            if slot not in set(dup_slots):
                event_stats["duplicate"]["false_alarms"] += 1

    metrics = {"confusion_matrix": matrix,
               "per_state": {}}
    for state in labels:
        metrics["per_state"][state] = {
            "precision": precision(state), "recall": recall(state),
            "false_positive_rate": fpr(state),
            "support": sum(matrix[state].values())}
    metrics["events"] = {}
    for name, stats in event_stats.items():
        metrics["events"][name] = dict(stats)
        metrics["events"][name]["detection_rate"] = (
            stats["detected"] / stats["total"] if stats["total"] else None)
    return metrics


def render_metrics(metrics):
    labels = [RAN_WITH_OUTPUT, RAN_EMPTY, NOT_RUN]
    out = io.StringIO()
    w = out.write
    w("逐窗口三态混淆矩阵（行=真值，列=巡检判定）\n")
    w(f"{'':<18}" + "".join(f"{s:<18}" for s in labels) + "\n")
    for a in labels:
        w(f"{a:<18}" + "".join(f"{metrics['confusion_matrix'][a][b]:<18}"
                               for b in labels) + "\n")
    w("\n逐状态指标\n")
    w(f"{'状态':<18}{'精确率':>10}{'召回率':>10}{'误报率(FPR)':>14}{'样本数':>10}\n")
    for state in labels:
        row = metrics["per_state"][state]
        def pct(v):
            return "-" if v is None else f"{v * 100:.2f}%"
        w(f"{state:<18}{pct(row['precision']):>10}{pct(row['recall']):>10}"
          f"{pct(row['false_positive_rate']):>14}{row['support']:>10}\n")
    w("\n注入场景事件级检出\n")
    w(f"{'场景':<18}{'检出/总数':>12}{'检出率':>10}{'误报事件数':>12}\n")
    names = {"missed_groups": "漏跑段", "stuck": "卡死",
             "duplicate": "补偿重复产出"}
    for key, cn in names.items():
        stats = metrics["events"][key]
        rate = "-" if stats["detection_rate"] is None else \
            f"{stats['detection_rate'] * 100:.2f}%"
        w(f"{cn:<18}{str(stats['detected']) + '/' + str(stats['total']):>12}"
          f"{rate:>10}{stats['false_alarms']:>12}\n")
    return out.getvalue()


def write_truth_csv(path, truth_rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["job_id", "slot_at", "true_state",
                         "injected_scenario", "note"])
        writer.writerows(truth_rows)


def write_verdict_csv(path, result):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["job_id", "slot_at", "state", "confidence", "basis"])
        for verdict in result["slot_verdicts"]:
            writer.writerow([verdict["job_id"], verdict["slot_at"],
                             verdict["state"], verdict["confidence"],
                             verdict["evidence"].get("basis", "")])


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

class InspectorTests(unittest.TestCase):
    def test_cron_expand(self):
        s = CronSchedule("*/15 9-17 * * 1-5", "UTC")
        self.assertEqual(s.minutes, {0, 15, 30, 45})
        self.assertEqual(s.hours, set(range(9, 18)))
        self.assertFalse(s._day_matches(2026, 3, 7))   # Saturday
        self.assertTrue(s._day_matches(2026, 3, 9))    # Monday

    def test_dst_spring_forward_skips_nonexistent(self):
        # 美东 2026-03-08 02:00->03:00，本地 02:05 不存在，不应产生 slot
        s = CronSchedule("5 2 * * *", "America/New_York")
        slots = list(s.iter_slots(parse_dt("2026-03-06T00:00:00Z"),
                                  parse_dt("2026-03-10T00:00:00Z")))
        local_times = [t.astimezone(ZoneInfo("America/New_York")) for t in slots]
        days = {t.date() for t in local_times}
        self.assertNotIn(__import__("datetime").date(2026, 3, 8), days)
        # 相邻两次触发间隔在 DST 日附近不为 24h，但每天最多一个 slot
        self.assertEqual(len(slots), 3)

    def test_dst_fall_back_no_duplicate_slot(self):
        # 美东 2026-11-01 02:00 回拨，01:30 只应触发一次
        s = CronSchedule("30 1 * * *", "America/New_York")
        slots = list(s.iter_slots(parse_dt("2026-10-31T00:00:00Z"),
                                  parse_dt("2026-11-03T00:00:00Z")))
        self.assertEqual(len(slots), 3)

    def test_three_states_demo(self):
        doc, truth = build_demo()
        result = inspect(doc)
        states = {
            (v["job_id"], v["slot_at"]): v["state"]
            for v in result["slot_verdicts"]}
        by_truth = {NOT_RUN: 0, RAN_EMPTY: 0, RAN_WITH_OUTPUT: 0}
        for job_id, slot_iso, true_state, _scenario, _note in truth:
            if true_state == PAUSED:
                self.assertEqual(states[(job_id, slot_iso)], PAUSED)
                continue
            self.assertEqual(states[(job_id, slot_iso)], true_state,
                             f"{job_id} {slot_iso}")
            by_truth[true_state] += 1
        self.assertGreater(by_truth[NOT_RUN], 0)
        self.assertGreater(by_truth[RAN_EMPTY], 0)
        self.assertGreater(by_truth[RAN_WITH_OUTPUT], 0)

    def test_findings_demo(self):
        doc, _ = build_demo()
        result = inspect(doc)
        kinds = {f["type"] for f in result["findings"]}
        self.assertIn(MISSED_RUN, kinds)
        self.assertIn(STUCK, kinds)
        self.assertIn(DUPLICATE_OUTPUT, kinds)
        self.assertIn(PAUSE_VIOLATION, kinds)

    def test_empty_run_distinguished_from_missed(self):
        doc = {
            "as_of": "2026-03-02T02:00:00Z",
            "horizon_start": "2026-03-01T00:00:00Z",
            "jobs": [{"job_id": "j", "cron": "0 * * * *",
                      "timezone": "UTC", "grace_minutes": 10,
                      "max_run_minutes": 20}],
            "batches": [],
            "run_records": [
                {"job_id": "j", "started_at": "2026-03-01T00:01:00Z",
                 "finished_at": "2026-03-01T00:05:00Z", "status": "success",
                 "window_start": "2026-03-01T00:00:00Z",
                 "window_end": "2026-03-01T01:00:00Z"}],
        }
        result = inspect(doc)
        states = {v["slot_at"]: v["state"] for v in result["slot_verdicts"]}
        self.assertEqual(states["2026-03-01T00:00:00Z"], RAN_EMPTY)
        self.assertEqual(states["2026-03-01T01:00:00Z"], NOT_RUN)

    def test_late_run_not_false_alarm(self):
        doc = {
            "as_of": "2026-03-01T04:00:00Z",
            "horizon_start": "2026-03-01T00:00:00Z",
            "jobs": [{"job_id": "j", "cron": "0 * * * *",
                      "timezone": "UTC", "grace_minutes": 20,
                      "max_run_minutes": 20}],
            "batches": [{
                "job_id": "j", "batch_no": 1,
                "window_start": "2026-03-01T00:00:00Z",
                "window_end": "2026-03-01T01:00:00Z",
                "produced_at": "2026-03-01T00:35:00Z",
                "row_count": 10, "watermark": "2026-03-01T00:59:00Z"}],
            "run_records": [],
        }
        result = inspect(doc)
        states = {v["slot_at"]: v["state"] for v in result["slot_verdicts"]}
        self.assertEqual(states["2026-03-01T00:00:00Z"], RAN_WITH_OUTPUT)
        self.assertFalse(any(f["type"] == MISSED_RUN
                             for f in result["findings"]
                             if f["evidence"].get("first_slot")
                             == "2026-03-01T00:00:00Z"))

    def test_benchmark_small_perfect(self):
        doc, truth = build_benchmark(n_jobs=40, seed=7)
        result = inspect(doc)
        metrics = score(result, truth)
        for state in (NOT_RUN, RAN_EMPTY, RAN_WITH_OUTPUT):
            self.assertEqual(
                metrics["per_state"][state]["recall"], 1.0,
                f"{state} recall not perfect: {metrics}")
            self.assertEqual(
                metrics["per_state"][state]["false_positive_rate"], 0.0,
                f"{state} FPR not zero: {metrics}")


def run_selftest(argv):
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(InspectorTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_inspect(args):
    doc = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = inspect(doc)
    _emit_result(result, args)
    return 1 if result["findings"] and not args.no_fail_code else 0


def cmd_demo(args):
    doc, truth = build_demo()
    result = inspect(doc)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "sample_input.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "sample_truth.csv").write_text(
        _truth_csv_text(truth), encoding="utf-8")
    (outdir / "sample_report.txt").write_text(
        render_report(result), encoding="utf-8")
    (outdir / "sample_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_verdict_csv(outdir / "sample_verdicts.csv", result)
    metrics = score(result, [[*t] for t in truth])
    (outdir / "sample_metrics.txt").write_text(
        render_metrics(metrics), encoding="utf-8")
    print(render_report(result))
    print(render_metrics(metrics))
    print(f"样例文件已写入 {outdir}/")
    return 0


def cmd_benchmark(args):
    doc, truth = build_benchmark(n_jobs=args.jobs, seed=args.seed)
    result = inspect(doc)
    metrics = score(result, truth)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "benchmark_input.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    write_truth_csv(outdir / "benchmark_truth.csv", truth)
    write_verdict_csv(outdir / "benchmark_verdicts.csv", result)
    (outdir / "benchmark_report.json").write_text(
        json.dumps({"summary": result["state_totals"],
                    "findings": result["finding_totals"],
                    "metrics": _jsonable(metrics)}, ensure_ascii=False,
                   indent=2), encoding="utf-8")
    (outdir / "benchmark_metrics.txt").write_text(
        render_metrics(metrics), encoding="utf-8")
    print(f"构造任务数: {args.jobs}  种子: {args.seed}")
    print(f"三态分布: {result['state_totals']}")
    print(f"发现分布: {result['finding_totals']}")
    print(render_metrics(metrics))
    print(f"明细数据已写入 {outdir}/")
    return 0


def _jsonable(value):
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _truth_csv_text(truth):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["job_id", "slot_at", "true_state",
                     "injected_scenario", "note"])
    for row in truth:
        writer.writerow(row)
    return buf.getvalue()


def _emit_result(result, args):
    text = render_report(result)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(text)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="基于任务产出的漏跑巡检（标准库）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("inspect", help="巡检输入 JSON")
    p.add_argument("input")
    p.add_argument("--report", help="文本报告输出路径")
    p.add_argument("--json-out", dest="json_out", help="JSON 结果输出路径")
    p.add_argument("--no-fail-code", dest="no_fail_code", action="store_true",
                   help="存在异常时也返回 0")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("demo", help="生成手工构造的样例场景与报告")
    p.add_argument("--outdir", default="artifacts")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("benchmark", help="批量构造场景并量化检出率/误报率")
    p.add_argument("--jobs", type=int, default=160)
    p.add_argument("--seed", type=int, default=20260310)
    p.add_argument("--outdir", default="artifacts")
    p.set_defaults(func=cmd_benchmark)

    p = sub.add_parser("selftest", help="运行内置自测")
    p.set_defaults(func=run_selftest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
