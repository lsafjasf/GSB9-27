"""缺口复现 + 修复版行为对照。运行：python3 reproduce.py

纯 VirtualClock 驱动，结果同步写入 sample_output.txt。
"""

from __future__ import annotations

from io import StringIO

from scheduler import (
    CatchUpPolicy,
    Disposition,
    MonotonicClock,
    Scheduler,
    VirtualClock,
)
from legacy_scheduler import LegacyScheduler

DISP_LABEL = {
    Disposition.FIRE: "fire        ",
    Disposition.MISSED_SKIP: "missed_skip ",
    Disposition.SUPPRESSED: "suppressed  ",
    Disposition.OVERLAP: "overlap     ",
}


def render_events(out, title, events):
    out.write(f"\n== {title} ==\n")
    out.write("job   seq  scheduled  observed  disposition    catchup  alert/error\n")
    for ev in events:
        note = ev.alert or ev.error or ""
        out.write(
            f"{ev.job:<4} {ev.seq:>3} {ev.scheduled_at:>9.0f} {ev.observed_at:>9.0f}  "
            f"{DISP_LABEL[ev.disposition]} {str(ev.is_catch_up):<7}  {note}\n"
        )


def main():
    buf = StringIO()
    buf.write("调度器停机补跑：修复前/后对照（时间单位为注入的虚拟秒）\n")

    # --- 修复前：停机跨过 3 个触发点，只补 1 次，统计缺口 2 ---
    clock = VirtualClock(0)
    legacy_runs = []
    legacy = LegacyScheduler(clock)
    legacy.add_job("agg", interval=10, start=0,
                   func=lambda now: legacy_runs.append(now))
    # t=0 正常运行，随后停机：10、20、30 三个点被跨过，t=35 恢复
    legacy.pump(0)
    legacy.pump(35)
    buf.write(
        f"\n[legacy] 停机跨过 t=10,20,30，t=35 恢复：实际执行 {len(legacy_runs)} 次"
        f"（t={[0] + legacy_runs[1:] if len(legacy_runs) > 1 else legacy_runs}），"
        f"应有 4 次（seq=0..3），统计缺口 = {4 - len(legacy_runs)}，且无任何告警。\n"
    )

    # --- 修复后：三种策略对照（停机跨过 10/20/30，t=35 恢复） ---
    for policy in (CatchUpPolicy.ALL, CatchUpPolicy.LATEST, CatchUpPolicy.SKIP):
        clk = VirtualClock(0)
        records = []
        sch = Scheduler(clk)
        sch.add_job("agg", interval=10, start=0, policy=policy,
                    func=lambda ctx: records.append((ctx.seq, ctx.scheduled_at)))
        sch.pump(0)    # 正常点 seq=0
        sch.pump(35)   # 恢复：错过 seq=1,2,3
        render_events(buf, f"fixed / {policy.value}", sch.events)
        counts = sch.counts()
        buf.write(f"counts={counts} alerts={len(sch.alerts)} "
                  f"executed_seqs={[r[0] for r in records]}\n")

    # --- 时钟跳跃 + 长任务重叠：执行中再次到期不得并发 ---
    clk = VirtualClock(0)
    import threading
    entered = threading.Event()
    release = threading.Event()
    concurrency = {"active": 0, "max": 0}
    clock_lock = threading.Lock()

    def slow(ctx):
        with clock_lock:
            concurrency["active"] += 1
            concurrency["max"] = max(concurrency["max"], concurrency["active"])
        if ctx.seq == 0:
            entered.set()
            release.wait(timeout=2)
        with clock_lock:
            concurrency["active"] -= 1

    sch = Scheduler(clk)
    sch.add_job("slow", interval=10, start=0,
                policy=CatchUpPolicy.ALL, func=slow)

    th = threading.Thread(target=sch.pump, kwargs={"now": 0})
    th.start()
    entered.wait(2)
    overlap_batch = sch.pump(25)   # seq=1,2 在执行期到期
    release.set()
    th.join()
    later = sch.pump(30)           # seq=3 正常点
    render_events(buf, "fixed / overlap protection", overlap_batch + later)
    buf.write(f"max concurrent executions of same job = {concurrency['max']} "
              f"(必须为 1), counts={sch.counts()}, alerts={len(sch.alerts)}\n")

    text = buf.getvalue()
    print(text)
    with open("sample_output.txt", "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    main()
