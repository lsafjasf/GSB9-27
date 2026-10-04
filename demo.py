"""演示四种场景的生命周期判定数据。运行：python3 demo.py"""

import threading

from session_lifecycle import Session, SessionConfig, SessionExpiredError


def show(title, snap):
    print(f"  [{title}]")
    print(f"    now={snap.now:>9.1f}  last_activity={snap.last_activity_at:>9.1f}  created={snap.created_at:.1f}")
    print(f"    idle_expires_at={snap.idle_expires_at:>9.1f}  absolute_expires_at={snap.absolute_expires_at:>9.1f}"
          f"  effective={snap.effective_expires_at:>9.1f}")
    print(f"    idle_remaining={snap.idle_remaining:>8.1f}s  absolute_remaining={snap.absolute_remaining:>8.1f}s"
          f"  -> {snap.state.value} ({snap.reason.value})")


def new(t0=0.0, idle=60.0, absolute=300.0):
    return Session(SessionConfig(idle_timeout=idle, absolute_timeout=absolute), now=t0)


print("== 场景 1：持续活跃（每 50s 续期，空闲窗口 60s，绝对期限 300s）==")
s = new()
for i in range(1, 7):
    t = i * 50.0
    try:
        r = s.renew(now=t)
        print(f"  续期@{t:>6.1f}: idle_expiry {r.before.idle_expires_at:>6.1f} -> {r.after.idle_expires_at:>6.1f}"
              f"  absolute_expiry {r.before.absolute_expires_at:.1f} -> {r.after.absolute_expires_at:.1f} (不变)")
    except SessionExpiredError as e:
        print(f"  续期@{t:>6.1f}: 被拒绝，原因={e.reason.value}")
        show("到期判定", e.snapshot)

print("\n== 场景 2：长时间挂起（无任何活动）==")
s = new()
show("t=59.9 仍活跃", s.inspect(now=59.9))
show("t=60.0 到达空闲边界", s.inspect(now=60.0))

print("\n== 场景 3：到达绝对期限（再活跃也救不回）==")
s = new()
for t in range(50, 291, 50):
    s.renew(now=float(t))  # 一直保持活跃直到 290
show("t=290 续期后（idle 到 350，被 absolute 钳制在 300）", s.inspect(now=290.0))
show("t=300 到达绝对边界", s.inspect(now=300.0))

print("\n== 场景 4：并发续期（32 线程，续期时刻 100.0~115.5）==")
times = [100.0 + i * 0.5 for i in range(32)]
s = new(t0=times[0], idle=60.0, absolute=10_000.0)
barrier = threading.Barrier(len(times))

def worker(t):
    barrier.wait()
    s.renew(now=t)  # 任何异常都会在线程内抛出，便于发现

threads = [threading.Thread(target=worker, args=(t,)) for t in times]
for th in threads: th.start()
for th in threads: th.join()
show("并发续期后的最终状态", s.inspect(now=max(times)))
print(f"  最终到期时刻 = max(续期时刻) + idle = {max(times)} + 60 = {max(times)+60.0}（与调度顺序无关）")
