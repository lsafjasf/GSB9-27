"""调度器回归测试：时间全部由 FakeClock 注入，无真实等待。"""
import threading
import unittest

from buggy_scheduler import BuggyScheduler
from scheduler import MisfirePolicy, Scheduler, Task


class FakeClock:
    def __init__(self, t: int = 0):
        self.t = t

    def __call__(self) -> int:
        return self.t

    def advance(self, dt: int) -> None:
        self.t += dt


def make_side_effect(counter: dict):
    def run(scheduled_at: int) -> None:
        counter["n"] = counter.get("n", 0) + 1
    return run


class BugReproTest(unittest.TestCase):
    """复现：旧实现停机后丢失触发点，修复版按策略补齐。"""

    def test_buggy_scheduler_loses_triggers(self):
        clock = FakeClock(0)
        counter = {}
        buggy = BuggyScheduler(clock)
        buggy.register("stats", 10, make_side_effect(counter))
        clock.advance(35)  # 停机 35s，跨过 t=10/20/30 三个触发点
        buggy.tick()
        self.assertEqual(counter["n"], 1)  # BUG：只补了一次，缺口 2 次

    def test_fixed_scheduler_recovers_all(self):
        clock = FakeClock(0)
        counter = {}
        sched = Scheduler(clock)
        sched.register(Task("stats", 10, make_side_effect(counter),
                            MisfirePolicy.RUN_ALL))
        clock.advance(35)
        sched.tick()
        self.assertEqual(counter["n"], 3)  # t=10/20/30 全部补跑


class CatchUpPolicyTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(0)
        self.counter = {}
        self.sched = Scheduler(self.clock)

    def _register(self, policy, interval=10):
        self.sched.register(Task("stats", interval,
                                 make_side_effect(self.counter), policy))

    def test_downtime_crosses_single_trigger(self):
        self._register(MisfirePolicy.RUN_ALL)
        self.clock.advance(15)  # 跨过 t=10 一次
        self.sched.tick()
        self.assertEqual(self.counter["n"], 1)
        self.assertEqual([r.scheduled_at for r in self.sched.records], [10])

    def test_downtime_crosses_multiple_triggers(self):
        self._register(MisfirePolicy.RUN_ALL)
        self.clock.advance(35)  # 跨过 t=10/20/30
        self.sched.tick()
        self.assertEqual(self.counter["n"], 3)
        self.assertEqual([r.scheduled_at for r in self.sched.records],
                         [10, 20, 30])

    def test_downtime_exactly_multiple_of_interval(self):
        self._register(MisfirePolicy.RUN_ALL)
        self.clock.advance(30)  # 恰好 3 个周期，边界触发点必须执行
        self.sched.tick()
        self.assertEqual(self.counter["n"], 3)
        self.assertEqual([r.scheduled_at for r in self.sched.records],
                         [10, 20, 30])
        # 下一触发点应为 t=40，不应锚定到 now+interval
        self.assertEqual(self.sched.tasks["stats"].next_trigger, 40)

    def test_long_downtime_run_all(self):
        self._register(MisfirePolicy.RUN_ALL)
        self.clock.advance(100_000)  # 长停机：10000 个触发点
        self.sched.tick()
        self.assertEqual(self.counter["n"], 10_000)

    def test_long_downtime_run_latest(self):
        self._register(MisfirePolicy.RUN_LATEST)
        self.clock.advance(100_000)
        self.sched.tick()
        self.assertEqual(self.counter["n"], 1)
        self.assertEqual(self.sched.records[0].scheduled_at, 100_000)
        superseded = [m for m in self.sched.missed
                      if m.reason == "superseded_by_latest"]
        self.assertEqual(len(superseded), 9_999)

    def test_long_downtime_skip_counts_and_alerts(self):
        alerts = []
        sched = Scheduler(self.clock, alert_handler=alerts.append)
        sched.register(Task("stats", 10, make_side_effect(self.counter),
                            MisfirePolicy.SKIP))
        self.clock.advance(100_000)
        sched.tick()
        self.assertEqual(self.counter.get("n", 0), 0)          # 零副作用
        skipped = [m for m in sched.missed if m.reason == "skipped_by_policy"]
        self.assertEqual(len(skipped), 10_000)                 # 跳过必须计数
        self.assertEqual(len(sched.alerts), 1)                 # 并告警
        self.assertEqual(len(alerts), 1)
        self.assertIn("10000", alerts[0])

    def test_normal_operation_no_catchup(self):
        self._register(MisfirePolicy.RUN_ALL)
        for _ in range(5):
            self.clock.advance(10)
            self.sched.tick()
        self.assertEqual(self.counter["n"], 5)
        self.assertEqual(self.sched.missed, [])


class IdempotencyTest(unittest.TestCase):
    def test_same_trigger_point_never_executes_twice(self):
        clock = FakeClock(0)
        counter = {}
        sched = Scheduler(clock)
        task = Task("stats", 10, make_side_effect(counter), MisfirePolicy.RUN_ALL)
        sched.register(task)
        clock.advance(10)
        sched.tick()
        sched.tick()  # 同一时刻重复 tick（卡顿重试场景）
        sched._execute(task, 10)  # 同一触发点被外部重复注入
        self.assertEqual(counter["n"], 1)  # 副作用只发生一次
        suppressed = [r for r in sched.records
                      if r.status == "duplicate_suppressed"]
        self.assertEqual(len(suppressed), 1)
        self.assertEqual(suppressed[0].scheduled_at, 10)

    def test_catchup_records_are_assertable(self):
        clock = FakeClock(0)
        counter = {}
        sched = Scheduler(clock)
        sched.register(Task("stats", 10, make_side_effect(counter),
                            MisfirePolicy.RUN_ALL))
        clock.advance(35)
        sched.tick()
        executed = [r for r in sched.records if r.status == "executed"]
        # 每个触发点恰好一条 executed 记录，且键唯一
        keys = [(r.task, r.scheduled_at) for r in executed]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual([r.scheduled_at for r in executed], [10, 20, 30])


class OverlapTest(unittest.TestCase):
    def test_long_running_task_never_concurrent(self):
        clock = FakeClock(0)
        log = []

        def slow(scheduled_at: int) -> None:
            log.append(("start", scheduled_at, clock()))
            clock.advance(25)  # 任务运行期间时钟跳过 2.5 个周期
            log.append(("end", scheduled_at, clock()))

        sched = Scheduler(clock)
        sched.register(Task("etl", 10, slow, MisfirePolicy.RUN_ALL))
        clock.advance(10)
        sched.tick()  # t=10 开始执行，结束时钟已到 35
        sched.tick()  # 补跑 t=20/30，各自又把时钟推远
        sched.tick()
        sched.tick()

        # 一致性：任意两次执行时间区间不重叠（串行），触发点不重复
        executed = [r for r in sched.records if r.status == "executed"]
        for prev, nxt in zip(executed, executed[1:]):
            self.assertLessEqual(prev.finished_at, nxt.started_at)
        keys = [r.scheduled_at for r in executed]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(keys, sorted(keys))

    def test_concurrent_thread_deferred_not_parallel(self):
        clock = FakeClock(0)
        counter = {}
        sched = Scheduler(clock)
        task = Task("stats", 10, make_side_effect(counter),
                    MisfirePolicy.RUN_ALL)
        sched.register(task)
        clock.advance(10)

        task.lock.acquire()  # 模拟另一线程正在执行该任务
        try:
            sched.tick()  # 触发点应被延迟而非并发执行
            self.assertEqual(counter.get("n", 0), 0)
            self.assertEqual(len(task.pending), 1)
            deferred = [m for m in sched.missed
                        if m.reason == "deferred_overlap"]
            self.assertEqual(len(deferred), 1)
        finally:
            task.lock.release()

        sched.tick()  # 锁释放后补跑
        self.assertEqual(counter["n"], 1)
        self.assertEqual(len(task.pending), 0)


class TriggerDetailTest(unittest.TestCase):
    def test_trigger_detail_covers_all_outcomes(self):
        clock = FakeClock(0)
        counter = {}
        sched = Scheduler(clock)
        sched.register(Task("all", 10, make_side_effect(counter),
                            MisfirePolicy.RUN_ALL))
        sched.register(Task("latest", 10, make_side_effect(counter),
                            MisfirePolicy.RUN_LATEST), start_at=0)
        clock.advance(30)
        sched.tick()
        detail = sched.trigger_detail()
        statuses = {d["status"] for d in detail}
        self.assertIn("executed", statuses)
        self.assertIn("superseded_by_latest", statuses)
        # 明细按触发点排序且包含全部任务
        self.assertEqual(detail, sorted(detail,
                         key=lambda d: (d["scheduled_at"], d["task"])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
