"""按数据版本失效的报表缓存（仅标准库）。

设计要点：
- DataRegistry 为每份底层数据维护单调递增版本号与最后变更时间。
- ReportCache 的每个缓存条目记录构建时的依赖版本快照；
  读取时逐依赖比对版本，只失效受影响的报表，绝不全量清空。
- 同一报表的并发未命中通过 single-flight 合并为一次重算。
- 重算失败时保留并返回旧值（stale fallback），不放大故障。
- 时间通过 time_fn 注入，测试可用假时钟。
"""

import threading
import time


class DataRegistry:
    """底层数据注册表：保存数据、版本号与最后变更时间。"""

    def __init__(self, time_fn=time.monotonic):
        self._time_fn = time_fn
        self._lock = threading.Lock()
        self._data = {}
        self._versions = {}
        self._changed_at = {}

    def set(self, key, value):
        """写入数据并递增版本（模拟底层数据变更）。"""
        with self._lock:
            self._data[key] = value
            self._versions[key] = self._versions.get(key, 0) + 1
            self._changed_at[key] = self._time_fn()

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def version(self, key):
        with self._lock:
            return self._versions.get(key, 0)

    def changed_at(self, key):
        """该 key 最后一次变更的时间；从未写入返回 None。"""
        with self._lock:
            return self._changed_at.get(key)


class CacheMetrics:
    """缓存运行指标：命中率、陈旧时长、重算次数。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.recomputes = 0
        self.recompute_failures = 0
        self._stale_serve_total = 0.0
        self._stale_serve_count = 0
        self._refresh_lag_total = 0.0
        self._refresh_lag_count = 0

    def record_hit(self):
        with self._lock:
            self.hits += 1

    def record_miss(self):
        with self._lock:
            self.misses += 1

    def record_recompute(self):
        with self._lock:
            self.recomputes += 1

    def record_recompute_failure(self):
        with self._lock:
            self.recompute_failures += 1

    def record_stale_serve(self, duration):
        """向调用方返回了已过期的数据，duration 为数据已陈旧的时长。"""
        with self._lock:
            self._stale_serve_total += duration
            self._stale_serve_count += 1

    def record_refresh_lag(self, duration):
        """一次失效到完成刷新之间的滞后时长。"""
        with self._lock:
            self._refresh_lag_total += duration
            self._refresh_lag_count += 1

    def snapshot(self):
        with self._lock:
            total = self.hits + self.misses
            return {
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": self.hits / total if total else 0.0,
                "recomputes": self.recomputes,
                "recompute_failures": self.recompute_failures,
                "avg_stale_serve": (
                    self._stale_serve_total / self._stale_serve_count
                    if self._stale_serve_count else 0.0
                ),
                "stale_serves": self._stale_serve_count,
                "avg_refresh_lag": (
                    self._refresh_lag_total / self._refresh_lag_count
                    if self._refresh_lag_count else 0.0
                ),
                "refreshes": self._refresh_lag_count,
            }


class _Entry:
    __slots__ = ("value", "versions", "built_at")

    def __init__(self, value, versions, built_at):
        self.value = value
        self.versions = versions
        self.built_at = built_at


class _Inflight:
    __slots__ = ("event", "error")

    def __init__(self):
        self.event = threading.Event()
        self.error = None


class ReportCache:
    """按数据版本失效 + single-flight 合并重算的报表缓存。"""

    def __init__(self, registry, time_fn=time.monotonic, failure_cooldown=1.0):
        self._registry = registry
        self._time_fn = time_fn
        self._failure_cooldown = failure_cooldown
        self._lock = threading.Lock()
        self._computes = {}   # name -> compute_fn(registry)
        self._deps = {}       # name -> tuple of dependency keys
        self._entries = {}    # name -> _Entry
        self._inflight = {}   # name -> _Inflight
        self._failed = {}     # name -> (failed_at, versions_at_failure)
        self.metrics = CacheMetrics()

    def register(self, name, dependencies, compute_fn):
        """注册报表：dependencies 为其依赖的数据 key，compute_fn(registry) 产出报表。"""
        with self._lock:
            self._computes[name] = compute_fn
            self._deps[name] = tuple(dependencies)

    def invalidate_dependents(self, data_key):
        """可选的主动失效入口：只失效依赖 data_key 的报表，不全量清空。"""
        with self._lock:
            for name, deps in self._deps.items():
                if data_key in deps:
                    self._entries.pop(name, None)

    def get(self, name):
        """读取报表。命中返回缓存；未命中合并并发重算；重算失败回退旧值。"""
        with self._lock:
            entry = self._entries.get(name)
            if entry is not None and self._is_fresh(entry, name):
                self.metrics.record_hit()
                return entry.value
            self.metrics.record_miss()
        return self._get_slow(name)

    # ---- 内部实现 ----

    def _is_fresh(self, entry, name):
        return all(
            self._registry.version(dep) == entry.versions[dep]
            for dep in self._deps[name]
        )

    def _stale_since(self, entry, name):
        """条目从何时开始陈旧：取所有已变更依赖的最早变更时间。"""
        since = None
        for dep in self._deps[name]:
            if self._registry.version(dep) != entry.versions[dep]:
                changed = self._registry.changed_at(dep)
                if changed is not None and (since is None or changed < since):
                    since = changed
        return since

    def _get_slow(self, name):
        while True:
            with self._lock:
                entry = self._entries.get(name)
                if entry is not None and self._is_fresh(entry, name):
                    return entry.value
                if entry is not None and self._in_failure_cooldown(name):
                    serve_stale = True
                else:
                    serve_stale = False
                if serve_stale:
                    pass  # 在锁外返回旧值，避免持锁记录指标
                else:
                    holder = self._inflight.get(name)
                    if holder is None:
                        holder = _Inflight()
                        self._inflight[name] = holder
                        leader = True
                    else:
                        leader = False

            if serve_stale:
                self._record_stale_serve(entry, name)
                return entry.value
            if not leader:
                holder.event.wait()
                if holder.error is not None:
                    # 领导者重算失败：跟随者同样回退旧值，避免失败风暴。
                    with self._lock:
                        entry = self._entries.get(name)
                    if entry is not None:
                        self._record_stale_serve(entry, name)
                        return entry.value
                    raise holder.error
                continue  # 领导者已写入新值，回到循环重新判定新鲜度

            try:
                value = self._recompute(name)
            except Exception as exc:
                with self._lock:
                    self._inflight.pop(name, None)
                    self._failed[name] = (
                        self._time_fn(),
                        tuple(self._registry.version(d) for d in self._deps[name]),
                    )
                    holder.error = exc
                    holder.event.set()
                    entry = self._entries.get(name)
                if entry is not None:
                    self._record_stale_serve(entry, name)
                    return entry.value
                raise
            with self._lock:
                self._inflight.pop(name, None)
                holder.event.set()
            return value

    def _in_failure_cooldown(self, name):
        """同一数据版本刚重算失败且仍在冷却期内时，直接回退旧值，避免失败风暴。"""
        fail = self._failed.get(name)
        if fail is None:
            return False
        failed_at, versions_at_failure = fail
        current = tuple(self._registry.version(d) for d in self._deps[name])
        if versions_at_failure != current:
            return False  # 数据又变了，值得重试
        return self._time_fn() - failed_at < self._failure_cooldown

    def _recompute(self, name):
        with self._lock:
            compute_fn = self._computes[name]
            old_entry = self._entries.get(name)
        if old_entry is not None:
            since = self._stale_since(old_entry, name)
        else:
            since = None
        self.metrics.record_recompute()
        try:
            value = compute_fn(self._registry)
        except Exception:
            self.metrics.record_recompute_failure()
            raise
        now = self._time_fn()
        versions = {dep: self._registry.version(dep) for dep in self._deps[name]}
        with self._lock:
            self._entries[name] = _Entry(value, versions, now)
            self._failed.pop(name, None)
        if since is not None:
            self.metrics.record_refresh_lag(now - since)
        return value

    def _record_stale_serve(self, entry, name):
        since = self._stale_since(entry, name)
        if since is not None:
            self.metrics.record_stale_serve(self._time_fn() - since)


class TTLReportCache:
    """对照组：固定过期时间的缓存（无版本感知，过期即全量重算该键）。"""

    def __init__(self, registry, ttl, time_fn=time.monotonic):
        self._registry = registry
        self._ttl = ttl
        self._time_fn = time_fn
        self._lock = threading.Lock()
        self._computes = {}
        self._deps = {}
        self._entries = {}  # name -> (value, expires_at, versions, built_at)
        self.metrics = CacheMetrics()

    def register(self, name, dependencies, compute_fn):
        with self._lock:
            self._computes[name] = compute_fn
            self._deps[name] = tuple(dependencies)

    def get(self, name):
        now = self._time_fn()
        with self._lock:
            entry = self._entries.get(name)
        if entry is not None and now < entry[1]:
            self.metrics.record_hit()
            self._maybe_record_stale(name, entry, now)
            return entry[0]
        self.metrics.record_miss()
        self.metrics.record_recompute()
        value = self._computes[name](self._registry)
        now = self._time_fn()
        versions = {dep: self._registry.version(dep) for dep in self._deps[name]}
        with self._lock:
            self._entries[name] = (value, now + self._ttl, versions, now)
        return value

    def _maybe_record_stale(self, name, entry, now):
        versions = entry[2]
        since = None
        for dep in self._deps[name]:
            if self._registry.version(dep) != versions[dep]:
                changed = self._registry.changed_at(dep)
                if changed is not None and (since is None or changed < since):
                    since = changed
        if since is not None:
            self.metrics.record_stale_serve(now - since)
