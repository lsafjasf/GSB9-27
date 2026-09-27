"""演示：停机 35s 后恢复，三种策略的补跑行为与触发明细输出。"""
from scheduler import MisfirePolicy, Scheduler, Task


class FakeClock:
    def __init__(self, t=0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def main():
    clock = FakeClock(0)
    sched = Scheduler(clock, alert_handler=lambda m: print(f"ALERT: {m}"))
    for name, policy in [("stats_all", MisfirePolicy.RUN_ALL),
                         ("cache_latest", MisfirePolicy.RUN_LATEST),
                         ("notify_skip", MisfirePolicy.SKIP)]:
        sched.register(Task(name, 10,
                            lambda ts, n=name: print(f"  run {n} @scheduled={ts}"),
                            policy))

    print("t=0 注册任务，interval=10s；随后停机 35s（跨过 t=10/20/30）")
    clock.advance(35)
    print("t=35 恢复，第一次 tick：")
    sched.tick()

    print("\n触发明细 trigger_detail()：")
    print(f"{'task':<14} {'scheduled':>9} {'started':>7} {'finished':>8}  status")
    for d in sched.trigger_detail():
        print(f"{d['task']:<14} {d['scheduled_at']:>9} "
              f"{str(d['started_at']):>7} {str(d['finished_at']):>8}  {d['status']}")

    print(f"\n告警记录：{sched.alerts}")


if __name__ == "__main__":
    main()
