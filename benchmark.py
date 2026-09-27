"""修复前后命中率 / 延迟 / 一致性对比。

运行：python3 benchmark.py
"""

import statistics
import threading
import time

from cache_buggy import BuggyCache
from cache_fixed import FixedCache
from fake_source import FakeSource

DURATION = 3.0
READERS = 8
SOURCE_LATENCY = 0.002


def run(cache_cls, inject_failures=False):
    source = FakeSource(latency=SOURCE_LATENCY)
    cache = cache_cls(source)
    cache.set("k", 0)
    lock = threading.Lock()
    committed = {"v": 0}
    stop = threading.Event()
    latencies = []
    stats = {"reads": 0, "stale": 0, "empty": 0, "errors": 0}

    def writer():
        n = 0
        while not stop.is_set():
            n += 1
            cache.set("k", n)
            with lock:
                committed["v"] = n
            time.sleep(0.005)

    def reader():
        while not stop.is_set():
            with lock:
                snap = committed["v"]
            t0 = time.perf_counter()
            try:
                value = cache.get("k")
            except Exception:
                stats["errors"] += 1
                continue
            latencies.append(time.perf_counter() - t0)
            stats["reads"] += 1
            if value is None:
                stats["empty"] += 1
            elif value < snap:
                stats["stale"] += 1

    def fault_injector():
        while not stop.is_set():
            time.sleep(0.5)
            source.fail_next(IOError("injected failure"))

    threads = [threading.Thread(target=writer)]
    threads += [threading.Thread(target=reader) for _ in range(READERS)]
    if inject_failures:
        threads.append(threading.Thread(target=fault_injector))
    for t in threads:
        t.start()
    time.sleep(DURATION)
    stop.set()
    for t in threads:
        t.join()

    total = cache.hits + cache.misses
    lat_sorted = sorted(latencies)
    p95 = lat_sorted[int(0.95 * (len(lat_sorted) - 1))] if lat_sorted else 0.0
    return {
        "hit_rate": cache.hits / total if total else 0.0,
        "avg_ms": statistics.mean(latencies) * 1000 if latencies else 0.0,
        "p95_ms": p95 * 1000,
        "source_reads": source.read_count,
        **stats,
    }


def report(title, buggy, fixed):
    print(f"\n== {title} ==")
    print(f"{'指标':<14}{'修复前(Buggy)':>16}{'修复后(Fixed)':>16}")
    rows = [
        ("命中率", f"{buggy['hit_rate']:.1%}", f"{fixed['hit_rate']:.1%}"),
        ("平均读延迟(ms)", f"{buggy['avg_ms']:.3f}", f"{fixed['avg_ms']:.3f}"),
        ("P95读延迟(ms)", f"{buggy['p95_ms']:.3f}", f"{fixed['p95_ms']:.3f}"),
        ("回源次数", str(buggy["source_reads"]), str(fixed["source_reads"])),
        ("读到旧值次数", str(buggy["stale"]), str(fixed["stale"])),
        ("读到空值次数", str(buggy["empty"]), str(fixed["empty"])),
        ("读取异常次数", str(buggy["errors"]), str(fixed["errors"])),
    ]
    for name, b, f in rows:
        print(f"{name:<14}{b:>16}{f:>16}")


if __name__ == "__main__":
    print(f"工作负载：{READERS} 读线程 + 1 写线程，单键，数据源延迟 {SOURCE_LATENCY*1000:.0f}ms，每组 {DURATION:.0f}s")
    report("场景A：无故障", run(BuggyCache), run(FixedCache))
    report("场景B：每0.5s注入一次回源失败", run(BuggyCache, True), run(FixedCache, True))
