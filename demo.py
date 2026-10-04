"""演示：引用计数数据 + 租约续期/超时回收时序（确定性假时钟，无需等待）。

运行：python3 demo.py
"""

from volume_refcount import LeaseExpired, NotMounted, VolumeBusy, VolumeManager


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


TTL = 30.0
clock = FakeClock()
mgr = VolumeManager(lease_ttl=TTL, clock=clock)
mgr.create_volume("data-vol")


def show_count(tag: str) -> None:
    print(f"  [t={clock.now:6.1f}s] {tag:<34} refcount={mgr.refcount('data-vol')}"
          f"  holders={mgr.holders('data-vol')}")


def line(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


# ---------------------------------------------------------------- 场景 1
line("场景 1：单使用者挂载/卸载 —— 每次操作后的引用计数")
show_count("初始")
rec = mgr.mount("data-vol", "pod-a")
show_count(f"mount(pod-a)  duplicate={rec.duplicate}")
after = mgr.unmount("data-vol", "pod-a")
show_count(f"unmount(pod-a) -> {after}")
mgr.delete_volume("data-vol")
print("  -> refcount=0，卷已安全删除")
mgr.create_volume("data-vol")

# ---------------------------------------------------------------- 场景 2
line("场景 2：多使用者 —— 计数逐一累加/递减")
for holder in ("pod-a", "pod-b", "pod-c"):
    mgr.mount("data-vol", holder)
    show_count(f"mount({holder})")
for holder in ("pod-b", "pod-a", "pod-c"):
    mgr.unmount("data-vol", holder)
    show_count(f"unmount({holder})")

# ---------------------------------------------------------------- 场景 3
line("场景 3：重复挂载同一卷 —— 计数保持 1，不错乱")
mgr.mount("data-vol", "pod-a")
show_count("mount(pod-a) 第 1 次")
for i in (2, 3):
    rec = mgr.mount("data-vol", "pod-a")
    show_count(f"mount(pod-a) 第 {i} 次  duplicate={rec.duplicate}")
mgr.unmount("data-vol", "pod-a")
show_count("unmount(pod-a) 一次即归零")

# ---------------------------------------------------------------- 场景 4
line("场景 4：异常退出 —— 租约续期与超时回收时序（TTL=30s，回收器每 10s 扫描）")
clock.now = 0.0
mgr.mount("data-vol", "pod-a")
mgr.mount("data-vol", "pod-b")
print(f"  [t={clock.now:5.1f}s] mount(pod-a), mount(pod-b)  "
      f"refcount={mgr.refcount('data-vol')}  租约均至 t=30s")

for tick in range(1, 8):  # 每 10s 一个节拍
    clock.advance(10)
    events = []
    if clock.now == 20.0:
        mgr.renew("data-vol", "pod-a")
        events.append("pod-a 续期(租约延至 t=50s)")
    if clock.now == 30.0:
        events.append("pod-b 异常退出，停止续期（租约 t=30s 到期）")
    reclaimed = mgr.reap_expired()
    for r in reclaimed:
        events.append(f"回收器回收 {r.holder_id}，refcount -> {r.refcount_after}")
    events.append(f"refcount={mgr.refcount('data-vol')} holders={mgr.holders('data-vol')}")
    print(f"  [t={clock.now:5.1f}s] " + "；".join(events))

print("  -> pod-b 未卸载即消失，租约超时由回收器兜底，pod-a 不受影响")

# ---------------------------------------------------------------- 场景 5
line("场景 5：保护性边界 —— 有引用禁止删除 / 过期续期被拒 / 重复卸载报错")
mgr.mount("data-vol", "pod-a")
show_count("重新 mount(pod-a)")
try:
    mgr.delete_volume("data-vol")
except VolumeBusy as exc:
    print(f"  delete_volume        -> VolumeBusy: {exc}")
clock.advance(31)  # pod-a 停止续期，租约超时
mgr.reap_expired()
show_count("pod-a 租约超时，回收器回收")
try:
    mgr.renew("data-vol", "pod-a")
except LeaseExpired as exc:
    print(f"  renew(过期租约)      -> LeaseExpired: {exc}")
try:
    mgr.unmount("data-vol", "pod-a")
except NotMounted as exc:
    print(f"  unmount(已被回收)    -> NotMounted: {exc}")
mgr.delete_volume("data-vol")
print("  -> refcount=0，卷安全删除，无数据丢失风险")
