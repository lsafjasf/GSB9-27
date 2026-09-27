"""Deadlock detection for Python threads (standard library only).

Maintains a wait-for graph of "thread -> lock -> owner thread" edges and
detects cycles on demand (or periodically via DeadlockMonitor).

False-positive exclusion rules
------------------------------
1. Condition-variable waits are NOT deadlock edges. A thread blocked in
   ``TrackedCondition.wait()`` has already released the underlying lock and
   is waiting for a *notification*, not for a lock held by another thread.
   Its ownership is removed from the graph for the duration of the wait.
2. Reentrant acquisition is NOT a wait edge. When the current owner of a
   ``TrackedRLock`` acquires it again, the call succeeds immediately, so no
   edge is registered. (Acquiring a plain non-reentrant ``TrackedLock``
   twice from the same thread IS reported as a self-deadlock cycle.)
3. Timed / non-blocking acquisitions are NOT deadlock edges. An
   ``acquire(timeout=...)`` or ``acquire(blocking=False)`` call returns on
   its own, so it can never be part of a permanent wait cycle. Such edges
   are recorded (kind="timed") for observability but excluded from cycle
   detection.

Only cycles in which *every* edge is an untimed, blocking lock acquisition
are reported as deadlocks.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass

# Wait edge kinds.
KIND_BLOCKING = "blocking"    # untimed blocking acquire -> participates in cycles
KIND_TIMED = "timed"          # timed / non-blocking acquire -> excluded
KIND_CONDITION = "condition"  # condition-variable wait -> excluded


@dataclass
class _Waiter:
    thread_ident: int
    thread_name: str
    lock_key: int
    lock_name: str
    kind: str
    since: float


@dataclass
class _Owner:
    thread_ident: int
    thread_name: str
    since: float


class _Registry:
    """Process-wide wait-for graph state."""

    def __init__(self) -> None:
        self._mu = threading.Lock()
        self.owners: dict[int, _Owner] = {}    # lock_key -> owner
        self.waiters: dict[int, _Waiter] = {}  # thread_ident -> waiter

    def add_owner(self, lock_key, thread_ident, thread_name):
        with self._mu:
            self.owners[lock_key] = _Owner(thread_ident, thread_name, time.monotonic())

    def remove_owner(self, lock_key):
        with self._mu:
            self.owners.pop(lock_key, None)

    def add_waiter(self, thread_ident, thread_name, lock_key, lock_name, kind):
        with self._mu:
            self.waiters[thread_ident] = _Waiter(
                thread_ident, thread_name, lock_key, lock_name, kind, time.monotonic()
            )

    def remove_waiter(self, thread_ident):
        with self._mu:
            self.waiters.pop(thread_ident, None)

    def snapshot(self):
        with self._mu:
            return dict(self.owners), dict(self.waiters)

    def reset(self):
        with self._mu:
            self.owners.clear()
            self.waiters.clear()


REGISTRY = _Registry()


def reset():
    """Clear all tracked state (mainly for tests)."""
    REGISTRY.reset()


def waiter_count():
    _, waiters = REGISTRY.snapshot()
    return len(waiters)


# ---------------------------------------------------------------------------
# Tracked primitives
# ---------------------------------------------------------------------------

_key_counter = itertools.count()


class TrackedLock:
    """Drop-in replacement for threading.Lock with wait-edge tracking."""

    def __init__(self, name: str | None = None):
        self._raw = threading.Lock()
        self._key = next(_key_counter)
        self._name = name or f"Lock-{self._key}"

    @property
    def name(self):
        return self._name

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        tid = threading.get_ident()
        tname = threading.current_thread().name
        timed = (not blocking) or (timeout is not None and timeout >= 0)
        kind = KIND_TIMED if timed else KIND_BLOCKING
        REGISTRY.add_waiter(tid, tname, self._key, self._name, kind)
        try:
            if not blocking:
                ok = self._raw.acquire(False)
            elif timed:
                ok = self._raw.acquire(True, timeout)
            else:
                ok = self._raw.acquire()
        finally:
            REGISTRY.remove_waiter(tid)
        if ok:
            REGISTRY.add_owner(self._key, tid, tname)
        return ok

    def release(self):
        REGISTRY.remove_owner(self._key)
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
    """Reentrant lock: re-acquisition by the owner is never a wait edge."""

    def __init__(self, name: str | None = None):
        self._raw = threading.Lock()
        self._key = next(_key_counter)
        self._name = name or f"RLock-{self._key}"
        self._owner: int | None = None
        self._count = 0

    @property
    def name(self):
        return self._name

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        tid = threading.get_ident()
        if self._owner == tid:
            # Reentrant acquire by the current owner: succeeds immediately,
            # so it must not appear in the wait-for graph.
            self._count += 1
            return True
        tname = threading.current_thread().name
        timed = (not blocking) or (timeout is not None and timeout >= 0)
        kind = KIND_TIMED if timed else KIND_BLOCKING
        REGISTRY.add_waiter(tid, tname, self._key, self._name, kind)
        try:
            if not blocking:
                ok = self._raw.acquire(False)
            elif timed:
                ok = self._raw.acquire(True, timeout)
            else:
                ok = self._raw.acquire()
        finally:
            REGISTRY.remove_waiter(tid)
        if ok:
            self._owner = tid
            self._count = 1
            REGISTRY.add_owner(self._key, tid, tname)
        return ok

    def release(self):
        tid = threading.get_ident()
        if self._owner != tid:
            raise RuntimeError("cannot release un-acquired lock")
        self._count -= 1
        if self._count == 0:
            self._owner = None
            REGISTRY.remove_owner(self._key)
            self._raw.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class TrackedCondition:
    """Condition variable whose waits are excluded from deadlock detection."""

    def __init__(self, lock: TrackedLock | None = None, name: str | None = None):
        self._lock = lock or TrackedLock(name=name)
        self._cond = threading.Condition(self._lock._raw)

    def acquire(self, *args, **kwargs):
        return self._lock.acquire(*args, **kwargs)

    def release(self):
        return self._lock.release()

    def wait(self, timeout: float | None = None) -> bool:
        tid = threading.get_ident()
        tname = threading.current_thread().name
        # A condition wait releases the lock and sleeps until notified:
        # remove ownership and mark the edge as non-deadlock-relevant.
        REGISTRY.remove_owner(self._lock._key)
        REGISTRY.add_waiter(tid, tname, self._lock._key, self._lock._name, KIND_CONDITION)
        try:
            return self._cond.wait(timeout)
        finally:
            REGISTRY.remove_waiter(tid)
            REGISTRY.add_owner(self._lock._key, tid, tname)

    def wait_for(self, predicate, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not predicate():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return predicate()
            if not self.wait(remaining):
                return predicate()
        return True

    def notify(self, n: int = 1):
        self._cond.notify(n)

    def notify_all(self):
        self._cond.notify_all()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------

@dataclass
class CycleStep:
    thread_ident: int
    thread_name: str
    holds_lock: str        # lock this thread holds that blocks the previous thread
    waits_for_lock: str    # lock this thread is blocked acquiring
    blocked_seconds: float


@dataclass
class DeadlockCycle:
    steps: list  # list[CycleStep], in wait order: step[i] waits on step[i+1]

    @property
    def max_blocked_seconds(self) -> float:
        return max(s.blocked_seconds for s in self.steps)

    def format(self) -> str:
        lines = []
        n = len(self.steps)
        for i, step in enumerate(self.steps):
            nxt = self.steps[(i + 1) % n]
            lines.append(
                f"  [{step.thread_name}] (tid={step.thread_ident}) "
                f"holds <{step.holds_lock}>, waits for <{step.waits_for_lock}> "
                f"held by [{nxt.thread_name}], blocked {step.blocked_seconds:.3f}s"
            )
        return "\n".join(lines)


def _find_cycles(edges: dict) -> list:
    """Find all disjoint cycles in a graph with out-degree <= 1."""
    cycles = []
    state = {}  # node -> 1 (in current path) / 2 (done)
    for start in edges:
        if state.get(start):
            continue
        path, index = [], {}
        node = start
        while node is not None and not state.get(node):
            state[node] = 1
            index[node] = len(path)
            path.append(node)
            node = edges.get(node)
        if node is not None and state.get(node) == 1:
            cycles.append(path[index[node]:])
        for n in path:
            state[n] = 2
    return cycles


def detect(now: float | None = None) -> list:
    """Detect all current deadlock cycles.

    Returns a list of DeadlockCycle, one per independent wait cycle,
    sorted by longest blocked duration first.
    """
    now = time.monotonic() if now is None else now
    owners, waiters = REGISTRY.snapshot()

    # Build thread -> thread edges. Only untimed blocking lock waits count.
    edges = {}
    for tid, w in waiters.items():
        if w.kind != KIND_BLOCKING:
            continue  # exclusion rules: timed / condition waits are not edges
        owner = owners.get(w.lock_key)
        if owner is None:
            continue  # lock currently unowned (race with release)
        edges[tid] = owner.thread_ident

    cycles = []
    for cycle_tids in _find_cycles(edges):
        steps = []
        n = len(cycle_tids)
        for i, tid in enumerate(cycle_tids):
            w = waiters[tid]
            prev_w = waiters[cycle_tids[(i - 1) % n]]
            steps.append(CycleStep(
                thread_ident=tid,
                thread_name=w.thread_name,
                holds_lock=prev_w.lock_name,  # lock that the previous waiter needs from us
                waits_for_lock=w.lock_name,
                blocked_seconds=now - w.since,
            ))
        cycles.append(DeadlockCycle(steps))

    cycles.sort(key=lambda c: c.max_blocked_seconds, reverse=True)
    return cycles


def report() -> str:
    """Human-readable report of all current deadlock cycles."""
    cycles = detect()
    if not cycles:
        return "no deadlocks detected"
    parts = [f"detected {len(cycles)} deadlock cycle(s):"]
    for i, cycle in enumerate(cycles, 1):
        parts.append(
            f"deadlock #{i} ({len(cycle.steps)} threads, "
            f"max blocked {cycle.max_blocked_seconds:.3f}s):"
        )
        parts.append(cycle.format())
    return "\n".join(parts)


class DeadlockMonitor:
    """Background thread that periodically checks for deadlocks."""

    def __init__(self, interval: float = 1.0, callback=None):
        self._interval = interval
        self._callback = callback or (lambda cycles: print(report(), flush=True))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="deadlock-monitor", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.wait(self._interval):
            cycles = detect()
            if cycles:
                self._callback(cycles)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
