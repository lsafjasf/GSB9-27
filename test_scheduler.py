"""调度器回归测试：python3 -m unittest -v test_scheduler"""

from __future__ import annotations

import threading
import unittest

from scheduler import (
    CatchUpPolicy,
    Disposition,
    Scheduler,
    SchedulerRunner,
    VirtualClock,
)
from legacy_scheduler import LegacyScheduler


def make(policy=CatchUpPolicy.ALL, start=0, interval=10):
    clock = VirtualClock(0)
    side_effects = []
    seen_keys = set()
    duplicate_keys = []

    def task(ctx):
        key = (ctx.job, ctx.seq)
        if key in seen_keys:
            duplicate_keys.append(key)
        seen_keys.add(key)
        side_effects.append((ctx.seq, ctx.scheduled_at, ctx.is_catch_up))
        return "ok"

    sch = Scheduler(clock)
    sch.add_job("j", interval=interval, start=start, policy=policy, func=task)
    return clock, sch, side_effects, duplicate_keys


class LegacyGapReproTest(unittest.TestCase):
    def test_legacy_loses_intermediate_triggers(self):
        """修复前实现：停机跨过 3 点只补 1 次，统计缺口可复现。"""
        clock = VirtualClock(0)
        runs = []
        legacy = LegacyScheduler(clock)
        legacy.add_job("agg", interval=10, start=0, func=lambda now: runs.append(now))
        legacy.pump(0)
        legacy.pump(35)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[1], 35)
        self.assertEqual(4 - len(runs), 2)  # seq=1,2 永久丢失


class CatchUpAllTest(unittest.TestCase):
    def test_single_missed_trigger(self):
        """停机跨越恰好一次触发点（恢复点 < 下一个点）。"""
        clock, sch, runs, dup = make()
        sch.pump(0)
        sch.pump(15)  # 错过 seq=1@10，seq=2@20 未到
        seqs = [r[0] for r in runs]
        self.assertEqual(seqs, [0, 1])
        catch = [ev for ev in sch.events if ev.disposition is Disposition.FIRE]
        self.assertFalse(catch[0].is_catch_up)
        self.assertTrue(catch[1].is_catch_up)
        self.assertEqual(catch[1].scheduled_at, 10)
        self.assertEqual(catch[1].observed_at, 15)
        self.assertEqual(sch.counts()["fire"], 2)
        self.assertEqual(sch.alerts, [])
        self.assertEqual(dup, [])

    def test_multiple_missed_triggers_in_order(self):
        """停机跨越多次触发点：按时间顺序全部补跑。"""
        clock, sch, runs, dup = make()
        sch.pump(0)
        sch.pump(35)  # 错过 10,20,30
        self.assertEqual([r[0] for r in runs], [0, 1, 2, 3])
        times = [r[1] for r in runs]
        self.assertEqual(times, [0, 10, 20, 30])
        self.assertTrue(all(r[2] for r in runs[1:]))
        self.assertEqual(sch.counts()["fire"], 4)
        self.assertEqual(dup, [])

    def test_exact_multiple_of_interval(self):
        """恢复时刻恰好落在触发点：最后一点是正常触发，不是补跑。"""
        clock, sch, runs, dup = make()
        sch.pump(0)
        sch.pump(30)  # seq=1@10 seq=2@20 补跑；seq=3@30 准点
        self.assertEqual([r[0] for r in runs], [0, 1, 2, 3])
        fire = [ev for ev in sch.events if ev.disposition is Disposition.FIRE]
        self.assertEqual([ev.is_catch_up for ev in fire],
                         [False, True, True, False])
        self.assertEqual(dup, [])

    def test_long_downtime_all_policy(self):
        """长时间停机（1000 个周期）：策略 ALL 全部补跑，顺序连续无缺。"""
        clock, sch, runs, dup = make(interval=1)
        sch.pump(0)
        sch.pump(1000)  # seq=1..1000，其中 1000 准点
        self.assertEqual(len(runs), 1001)
        self.assertEqual([r[0] for r in runs], list(range(1001)))
        self.assertEqual(sch.counts()["fire"], 1001)
        self.assertEqual(dup, [])


class CatchUpLatestTest(unittest.TestCase):
    def test_latest_only_fires_most_recent(self):
        clock, sch, runs, dup = make(CatchUpPolicy.LATEST)
        sch.pump(0)
        sch.pump(35)  # 10,20 折叠；30 作为最近错过点补跑
        self.assertEqual([r[0] for r in runs], [0, 3])
        counts = sch.counts()
        self.assertEqual(counts["fire"], 2)
        self.assertEqual(counts["suppressed"], 2)
        self.assertEqual(counts["missed_skip"], 0)
        suppressed = [ev for ev in sch.events
                      if ev.disposition is Disposition.SUPPRESSED]
        self.assertEqual([ev.seq for ev in suppressed], [1, 2])
        self.assertEqual(sch.alerts, [])  # 折叠是有意策略，不告警
        self.assertEqual(dup, [])

    def test_latest_long_downtime_is_bounded(self):
        """LATEST 在长时间停机下只执行一次，避免补跑风暴。"""
        clock, sch, runs, dup = make(CatchUpPolicy.LATEST, interval=1)
        sch.pump(0)
        sch.pump(1_000_000.5)  # 非对齐恢复点：seq=1_000_000 是最近错过点
        self.assertEqual([r[0] for r in runs], [0, 1_000_000])
        self.assertEqual(sch.counts()["fire"], 2)
        self.assertEqual(sch.counts()["suppressed"], 999_999)
        self.assertEqual(dup, [])

    def test_latest_single_miss(self):
        clock, sch, runs, dup = make(CatchUpPolicy.LATEST)
        sch.pump(0)
        sch.pump(15)
        self.assertEqual([r[0] for r in runs], [0, 1])
        self.assertEqual(sch.counts()["suppressed"], 0)


class CatchUpSkipTest(unittest.TestCase):
    def test_skip_counts_and_alerts(self):
        clock, sch, runs, dup = make(CatchUpPolicy.SKIP)
        sch.pump(0)
        sch.pump(35)
        self.assertEqual([r[0] for r in runs], [0])  # 全部跳过
        counts = sch.counts()
        self.assertEqual(counts["missed_skip"], 3)
        self.assertEqual(counts["fire"], 1)
        self.assertEqual(len(sch.alerts), 1)
        self.assertIn("3 trigger(s)", sch.alerts[0])
        missed = [ev for ev in sch.events
                  if ev.disposition is Disposition.MISSED_SKIP]
        self.assertEqual([ev.seq for ev in missed], [1, 2, 3])
        self.assertTrue(all(ev.alert for ev in missed))
        # 回调式告警同样可达
        alerts_cb = []
        sch2 = Scheduler(VirtualClock(0), on_alert=lambda m, evs: alerts_cb.append(m))
        sch2.add_job("j2", 10, lambda ctx: None, start=0,
                     policy=CatchUpPolicy.SKIP)
        sch2.pump(25)
        self.assertEqual(len(alerts_cb), 1)
        self.assertEqual(dup, [])

    def test_skip_then_resume_normal(self):
        """跳过后下个准点照常执行，游标不回退。"""
        clock, sch, runs, dup = make(CatchUpPolicy.SKIP)
        sch.pump(0)
        sch.pump(35)
        sch.pump(40)  # seq=4 准点
        self.assertEqual([r[0] for r in runs], [0, 4])
        self.assertEqual(sch.counts()["missed_skip"], 3)
        self.assertEqual(sch.counts()["fire"], 2)


class IdempotencyTest(unittest.TestCase):
    def test_repeated_pump_does_not_refire(self):
        """恢复后 pump 被重复调用（看门狗/人工重放）不得产生第二次副作用。"""
        for policy in CatchUpPolicy:
            with self.subTest(policy=policy):
                clock, sch, runs, dup = make(policy)
                sch.pump(0)
                sch.pump(35)
                for _ in range(5):
                    sch.pump(35)
                    sch.pump(36)
                if policy is CatchUpPolicy.ALL:
                    self.assertEqual([r[0] for r in runs], [0, 1, 2, 3])
                elif policy is CatchUpPolicy.LATEST:
                    self.assertEqual([r[0] for r in runs], [0, 3])
                else:
                    self.assertEqual([r[0] for r in runs], [0])
                self.assertEqual(dup, [])

    def test_failing_task_is_not_replayed(self):
        """任务抛异常：事件留痕（error），但该触发点不重放。"""
        clock = VirtualClock(0)
        calls = []

        def flaky(ctx):
            calls.append(ctx.seq)
            if ctx.seq == 1:
                raise RuntimeError("boom")

        sch = Scheduler(clock)
        sch.add_job("j", 10, flaky, start=0)
        sch.pump(0)
        sch.pump(20)
        sch.pump(25)  # 再次驱动不得重放 seq=1
        self.assertEqual(calls, [0, 1, 2])
        ev1 = [ev for ev in sch.events if ev.seq == 1][0]
        self.assertIs(ev1.disposition, Disposition.FIRE)
        self.assertIn("boom", ev1.error)
        self.assertEqual(sch.fired_seqs("j"), [0, 1, 2])


class OverlapTest(unittest.TestCase):
    def test_no_concurrent_execution_of_same_job(self):
        clock = VirtualClock(0)
        entered = threading.Event()
        release = threading.Event()
        state = {"active": 0, "max": 0}
        lock = threading.Lock()

        def slow(ctx):
            with lock:
                state["active"] += 1
                state["max"] = max(state["max"], state["active"])
            if ctx.seq == 0:
                entered.set()
                release.wait(timeout=2)
            with lock:
                state["active"] -= 1

        sch = Scheduler(clock)
        sch.add_job("slow", 10, slow, start=0)
        th = threading.Thread(target=sch.pump, kwargs={"now": 0})
        th.start()
        self.assertTrue(entered.wait(2))
        batch = sch.pump(25)  # seq=1@10 seq=2@20 在执行期到期
        self.assertEqual([ev.disposition for ev in batch],
                         [Disposition.OVERLAP, Disposition.OVERLAP])
        self.assertEqual(len(sch.alerts), 1)
        self.assertIn("2 trigger(s)", sch.alerts[0])
        release.set()
        th.join()
        # 执行结束后下一个准点正常执行
        later = sch.pump(30)
        self.assertEqual([ev.disposition for ev in later], [Disposition.FIRE])
        self.assertEqual([ev.seq for ev in later], [3])
        self.assertEqual(state["max"], 1)  # 同任务并发度始终为 1
        self.assertEqual(sch.counts()["overlap"], 2)

    def test_overlap_and_catchup_policy_do_not_double_fire(self):
        """重叠期到期的点全部 overlap；任务结束后不会因补跑策略再执行一次。"""
        clock = VirtualClock(0)
        entered = threading.Event()
        release = threading.Event()

        def slow(ctx):
            if ctx.seq == 0:
                entered.set()
                release.wait(timeout=2)

        sch = Scheduler(clock)
        sch.add_job("slow", 10, slow, start=0, policy=CatchUpPolicy.ALL)
        th = threading.Thread(target=sch.pump, kwargs={"now": 0})
        th.start()
        entered.wait(2)
        sch.pump(35)  # seq=1,2,3 全部 overlap（游标前移，不再补跑）
        release.set()
        th.join()
        sch.pump(35)
        seqs = [ev.seq for ev in sch.events]
        self.assertEqual(sorted(seqs), [0, 1, 2, 3])
        self.assertEqual(
            sorted(ev.disposition for ev in sch.events),
            sorted([Disposition.FIRE, Disposition.OVERLAP,
                    Disposition.OVERLAP, Disposition.OVERLAP]),
        )


class ClockSemanticsTest(unittest.TestCase):
    def test_monotonic_schedule_no_gap(self):
        """无停机时逐周期驱动，每个点恰好一次，全部准点。"""
        clock, sch, runs, dup = make()
        for t in range(0, 51, 10):
            sch.pump(t)
        self.assertEqual([r[0] for r in runs], [0, 1, 2, 3, 4, 5])
        self.assertTrue(not any(r[2] for r in runs))

    def test_clock_go_back_is_ignored(self):
        """时钟回拨：忽略整批处理，不产生重复副作用。"""
        clock, sch, runs, dup = make()
        sch.pump(20)
        self.assertEqual([r[0] for r in runs], [0, 1, 2])
        batch = sch.pump(5)  # 回拨
        self.assertEqual(batch, [])
        self.assertEqual([r[0] for r in runs], [0, 1, 2])
        sch.pump(30)
        self.assertEqual([r[0] for r in runs], [0, 1, 2, 3])

    def test_multiple_jobs_independent(self):
        clock = VirtualClock(0)
        log_a, log_b = [], []
        sch = Scheduler(clock)
        sch.add_job("a", 10, lambda ctx: log_a.append(ctx.seq), start=0)
        sch.add_job("b", 15, lambda ctx: log_b.append(ctx.seq), start=0)
        sch.pump(30)
        self.assertEqual(log_a, [0, 1, 2, 3])
        self.assertEqual(log_b, [0, 1, 2])


class RunnerSmokeTest(unittest.TestCase):
    def test_runner_recovers_after_virtual_pause_simulation(self):
        """真实线程 + 真实单调时钟：runner 周期 pump，停机后恢复无重复。"""
        from scheduler import MonotonicClock
        sch = Scheduler(MonotonicClock())
        seqs = []
        lock = threading.Lock()
        def record(ctx):
            with lock:
                seqs.append(ctx.seq)
        sch.add_job("j", 0.02, record)
        runner = SchedulerRunner(sch, max_sleep=0.005)
        runner.start()
        try:
            threading.Event().wait(0.07)
        finally:
            runner.stop()
        self.assertGreaterEqual(len(seqs), 2)
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
