"""
deadlock_detector - 纯标准库的进程内死锁检测库.

核心思想:
    维护一张 "等待图" (wait-for graph):
        边  waiter_tid -> holder_tid  表示 "线程 waiter 正在等待 holder 持有的某把锁".
    图中出现有向环即死锁. 检测时输出完整环路径与每个持有者的持锁/阻塞时长.

误判排除规则 (详见 DEADLOCK_DETECTOR.md):
    1. 条件变量等待 (Condition.wait): 线程进入 wait 时主动释放底层锁,
       等待期间不登记任何等待边, 唤醒时对底层锁的重新获取也不登记.
    2. 可重入锁 (RLock): 同一线程重复 acquire 自己持有的锁不产生等待边,
       也绝不产生自环.
       注意: 该规则只适用于 RLock. 对不可重入的普通 Lock, 同一线程在
       未释放时再次 acquire 会自锁 (self-deadlock): 这是真实的自等待死锁,
       必须登记 waiter == holder 的自环边并检出.
    3. 超时锁 (acquire(timeout=...)): 带超时的等待边会被标记 expires_at,
       环检测时整条边被忽略 -- 它迟早会自行断开, 不可能构成真正的死锁环.
    4. 非阻塞 acquire(blocking=False): 不存在等待, 不登记边.

用法:
    from deadlock_detector import TrackedLock, TrackedRLock, TrackedCondition, detector
    lock = TrackedLock("my-lock")
    ...
    reports = detector.detect()          # 手动检测
    for r in reports:
        print(r.format())
    # 或后台周期检测:
    detector.start_monitor(interval=1.0)
"""

import threading
import time

__all__ = [
    "TrackedLock",
    "TrackedRLock",
    "TrackedCondition",
    "CycleReport",
    "DeadlockDetector",
    "detector",
]


class _Edge:
    """一条等待边: waiter 等待 holder 持有的 lock_name."""

    __slots__ = ("waiter", "holder", "lock_key", "lock_name", "since", "expires_at")

    def __init__(self, waiter, holder, lock_key, lock_name, since, expires_at):
        self.waiter = waiter
        self.holder = holder
        self.lock_key = lock_key
        self.lock_name = lock_name
        self.since = since
        self.expires_at = expires_at  # None 表示无限期阻塞等待

    @property
    def has_timeout(self):
        return self.expires_at is not None


class CycleReport:
    """一个死锁环的检测报告."""

    def __init__(self, edges, holders, names, now):
        # edges: 构成环的 _Edge 列表, 顺序即环路径方向 (谁等谁).
        self.edges = edges
        self.detected_at = now
        self.thread_names = [names.get(e.waiter, str(e.waiter)) for e in edges]
        # 每个等待者的已等待时长
        self.waits = [(names.get(e.waiter, str(e.waiter)), e.lock_name,
                       names.get(e.holder, str(e.holder)), now - e.since)
                      for e in edges]
        # 环上每个持有者的持锁时长 (持有者同时也是环上的等待者)
        self.holds = []
        for e in edges:
            holder_info = holders.get(e.lock_key)
            held = now - holder_info[1] if holder_info else 0.0
            self.holds.append((names.get(e.holder, str(e.holder)), e.lock_name, held))
        # 环的阻塞时长 = 环上最老的等待边的等待时长
        self.blocked_seconds = max(now - e.since for e in edges)

    def format(self):
        lines = [
            "[DEADLOCK] cycle: blocked %.3fs, %d threads"
            % (self.blocked_seconds, len(self.edges)),
            "  wait chain:",
        ]
        for waiter, lock_name, holder, waited in self.waits:
            lines.append("    %s --(waits %.3fs on lock '%s')--> %s"
                         % (waiter, waited, lock_name, holder))
        lines.append("  holders:")
        for holder, lock_name, held in self.holds:
            lines.append("    %s holds lock '%s' for %.3fs"
                         % (holder, lock_name, held))
        return "\n".join(lines)


class DeadlockDetector:
    """等待图注册表 + 环检测器 (单例使用, 见模块底部 detector)."""

    def __init__(self):
        self._mu = threading.Lock()
        self._edges = {}         # waiter_tid -> _Edge (一个线程同一时刻只阻塞在一把锁上)
        self._holders = {}       # lock_key -> (holder_tid, since)
        self._cond_waiters = set()  # 正处于 Condition.wait 的线程 (排除规则 1)
        self._names = {}         # tid -> 线程名
        self._monitor = None

    # ---- 注册接口 (由 Tracked* 包装类调用) ----

    def _note_thread(self, tid):
        if tid not in self._names:
            self._names[tid] = threading.current_thread().name

    def set_holder(self, lock_key, tid):
        with self._mu:
            self._note_thread(tid)
            self._holders[lock_key] = (tid, time.monotonic())

    def clear_holder(self, lock_key, tid):
        with self._mu:
            info = self._holders.get(lock_key)
            if info and info[0] == tid:
                del self._holders[lock_key]

    def holder_of(self, lock_key):
        with self._mu:
            info = self._holders.get(lock_key)
            return info[0] if info else None

    def add_wait_edge(self, waiter, holder, lock_key, lock_name, expires_at):
        with self._mu:
            self._note_thread(waiter)
            if waiter in self._cond_waiters:
                return  # 排除规则 1: 条件变量等待期间不登记
            # waiter == holder 的自环不能一刀切忽略:
            # RLock 的同线程重入在 TrackedRLock 中已提前放行, 根本不会走到这里;
            # 能走到这里的自环是普通 Lock 上 "持锁线程再次 acquire 自己" 的
            # 自锁, 属于真实死锁, 必须登记并参与成环检测.
            self._edges[waiter] = _Edge(waiter, holder, lock_key, lock_name,
                                        time.monotonic(), expires_at)

    def remove_wait_edge(self, waiter):
        with self._mu:
            self._edges.pop(waiter, None)

    def enter_cond_wait(self, lock_key, tid):
        """Condition.wait 开始: 线程释放了底层锁, 清除持有者记录并标记."""
        with self._mu:
            self._note_thread(tid)
            info = self._holders.get(lock_key)
            if info and info[0] == tid:
                del self._holders[lock_key]
            self._edges.pop(tid, None)
            self._cond_waiters.add(tid)

    def exit_cond_wait(self, lock_key, tid):
        """Condition.wait 返回: 线程已重新持有底层锁."""
        with self._mu:
            self._cond_waiters.discard(tid)
            self._holders[lock_key] = (tid, time.monotonic())

    # ---- 检测 ----

    def _snapshot(self):
        with self._mu:
            now = time.monotonic()
            edges = []
            for e in self._edges.values():
                if e.waiter in self._cond_waiters:
                    continue                      # 排除规则 1
                if e.has_timeout:
                    continue                      # 排除规则 3: 超时等待边不参与成环
                edges.append(e)
            return edges, dict(self._holders), dict(self._names), now

    @staticmethod
    def _elementary_cycles(edges):
        """枚举等待图中的所有基本环 (每个环只报一次)."""
        adj = {}
        for e in edges:
            adj.setdefault(e.waiter, []).append(e)
        nodes = sorted(adj)
        index = {n: i for i, n in enumerate(nodes)}
        cycles = []
        for start in nodes:
            path = [start]
            path_edges = []
            on_path = {start}
            stack = [iter(adj[start])]
            while stack:
                advanced = False
                for e in stack[-1]:
                    nxt = e.holder
                    if nxt not in adj:
                        continue  # 持有者自己不在等待 -> 死路
                    if index.get(nxt, -1) < index[start]:
                        continue  # 只从环上最小编号节点出发, 去重
                    if nxt == start:
                        # path 长度为 1 时是 waiter == holder 的自环 (普通
                        # Lock 自锁), 同样是真实死锁环, 必须报告.
                        cycles.append(list(path_edges) + [e])
                        continue
                    if nxt in on_path:
                        continue
                    path.append(nxt)
                    path_edges.append(e)
                    on_path.add(nxt)
                    stack.append(iter(adj[nxt]))
                    advanced = True
                    break
                if not advanced:
                    stack.pop()
                    if len(path) > 1:
                        on_path.discard(path.pop())
                        path_edges.pop()
        return cycles

    def detect(self):
        """检测当前所有死锁环, 按阻塞时长降序返回 CycleReport 列表."""
        edges, holders, names, now = self._snapshot()
        reports = [CycleReport(c, holders, names, now)
                   for c in self._elementary_cycles(edges)]
        reports.sort(key=lambda r: r.blocked_seconds, reverse=True)
        return reports

    def _reset(self):
        """清空全部运行状态 (仅供测试隔离使用)."""
        with self._mu:
            self._edges.clear()
            self._holders.clear()
            self._cond_waiters.clear()
            self._names.clear()

    # ---- 后台监控 (可选) ----

    def start_monitor(self, interval=1.0, callback=None):
        """启动后台线程周期检测; callback 缺省为打印报告."""
        if self._monitor and self._monitor.is_alive():
            return self._monitor

        def _default_callback(reports):
            for r in reports:
                print(r.format(), flush=True)

        cb = callback or _default_callback

        def _loop():
            while True:
                time.sleep(interval)
                reports = self.detect()
                if reports:
                    cb(reports)

        self._monitor = threading.Thread(target=_loop, daemon=True,
                                         name="deadlock-monitor")
        self._monitor.start()
        return self._monitor


# 全局单例
detector = DeadlockDetector()


# ---- 锁包装类 ----

_lock_counter = [0]
_lock_counter_mu = threading.Lock()


def _next_name(prefix):
    with _lock_counter_mu:
        _lock_counter[0] += 1
        return "%s-%d" % (prefix, _lock_counter[0])


class TrackedLock:
    """threading.Lock 的检测包装. 接口与 Lock 一致."""

    def __init__(self, name=None):
        self._raw = threading.Lock()
        self.name = name or _next_name("lock")
        self._key = ("lock", id(self))

    def acquire(self, blocking=True, timeout=-1):
        me = threading.get_ident()
        if not blocking:
            ok = self._raw.acquire(False)
            if ok:
                detector.set_holder(self._key, me)
            return ok
        has_timeout = timeout is not None and timeout >= 0
        holder = detector.holder_of(self._key)
        if holder is not None:
            # 普通 Lock 不可重入: holder == me 时再次阻塞式 acquire 是
            # 自锁 (self-deadlock), 登记自环边让检测器能发现它.
            expires = time.monotonic() + timeout if has_timeout else None
            detector.add_wait_edge(me, holder, self._key, self.name, expires)
        try:
            if has_timeout:
                ok = self._raw.acquire(True, timeout)
            else:
                ok = self._raw.acquire()
        finally:
            detector.remove_wait_edge(me)
        if ok:
            detector.set_holder(self._key, me)
        return ok

    def release(self):
        me = threading.get_ident()
        # 先清持有者记录再释放: 宁可漏边也不留假边 (控制误判)
        detector.clear_holder(self._key, me)
        self._raw.release()

    def locked(self):
        return self._raw.locked()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class TrackedRLock:
    """threading.RLock 的检测包装. 同线程重入不产生等待边."""

    def __init__(self, name=None):
        self._raw = threading.RLock()
        self.name = name or _next_name("rlock")
        self._key = ("rlock", id(self))
        self._owner = None
        self._count = 0

    def acquire(self, blocking=True, timeout=-1):
        me = threading.get_ident()
        if self._owner == me:
            # 排除规则 2: 可重入, 不可能阻塞, 直接放行
            self._raw.acquire()
            self._count += 1
            return True
        if not blocking:
            ok = self._raw.acquire(False)
            if ok:
                self._owner, self._count = me, 1
                detector.set_holder(self._key, me)
            return ok
        has_timeout = timeout is not None and timeout >= 0
        holder = detector.holder_of(self._key)
        if holder is not None and holder != me:
            expires = time.monotonic() + timeout if has_timeout else None
            detector.add_wait_edge(me, holder, self._key, self.name, expires)
        try:
            if has_timeout:
                ok = self._raw.acquire(True, timeout)
            else:
                ok = self._raw.acquire()
        finally:
            detector.remove_wait_edge(me)
        if ok:
            self._owner, self._count = me, 1
            detector.set_holder(self._key, me)
        return ok

    def release(self):
        me = threading.get_ident()
        self._count -= 1
        if self._count == 0:
            self._owner = None
            detector.clear_holder(self._key, me)
        self._raw.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class TrackedCondition:
    """条件变量包装. wait 期间的等待不计入死锁 (排除规则 1)."""

    def __init__(self, lock=None, name=None):
        self._tracked = lock if lock is not None else TrackedRLock()
        self.name = name or _next_name("cond")
        # threading.Condition 直接作用于底层原生锁, wait 的内部释放/重取
        # 不经过 Tracked 包装, 天然绕过等待边登记.
        self._cond = threading.Condition(self._tracked._raw)

    def acquire(self, *args, **kwargs):
        return self._tracked.acquire(*args, **kwargs)

    def release(self):
        return self._tracked.release()

    def wait(self, timeout=None):
        me = threading.get_ident()
        lock = self._tracked
        # cond.wait 会在底层完全释放锁 (RLock 则释放全部递归层), 唤醒后再恢复.
        # 包装层的所有权记录必须同步, 否则会被其他线程的 acquire 篡改.
        saved_count = getattr(lock, "_count", 1)
        detector.enter_cond_wait(lock._key, me)
        if isinstance(lock, TrackedRLock):
            lock._owner = None
            lock._count = 0
        try:
            return self._cond.wait(timeout)
        finally:
            if isinstance(lock, TrackedRLock):
                lock._owner = me
                lock._count = saved_count
            detector.exit_cond_wait(lock._key, me)

    def wait_for(self, predicate, timeout=None):
        endtime = None if timeout is None else time.monotonic() + timeout
        while not predicate():
            remaining = None if endtime is None else endtime - time.monotonic()
            if remaining is not None and remaining <= 0:
                return False
            if not self.wait(remaining):
                return False
        return True

    def notify(self, n=1):
        self._cond.notify(n)

    def notify_all(self):
        self._cond.notify_all()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


if __name__ == "__main__":
    # 演示: 构造一个两线程死锁, 由后台监控线程打印报告
    lock_a = TrackedLock("lock-A")
    lock_b = TrackedLock("lock-B")
    ready = threading.Event()

    def worker(name, first, second):
        first.acquire()
        ready.wait()
        time.sleep(0.05)
        second.acquire()  # 互相等待 -> 死锁

    t1 = threading.Thread(target=worker, args=("T1", lock_a, lock_b), name="T1")
    t2 = threading.Thread(target=worker, args=("T2", lock_b, lock_a), name="T2")
    t1.start()
    t2.start()
    ready.set()

    detector.start_monitor(interval=0.5)
    time.sleep(2.0)
    reports = detector.detect()
    print("detected %d deadlock cycle(s)" % len(reports))
    # 清理: Lock 允许跨线程释放, 解开死锁让演示退出
    lock_a.release()
    lock_b.release()
    t1.join()
    t2.join()
