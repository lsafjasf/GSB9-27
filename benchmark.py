"""Fallback-cost benchmark: fallback ratio & read latency vs lag threshold.

Scenario: replica lag is a steady 100 ms. A writer thread updates shared
keys continuously; reader sessions mostly read shared keys and occasionally
write-then-read their own key (the RYW-critical path). Each node has a
simulated 1 ms query RTT.
"""
import threading
import time

from db_cluster import Primary, Replica
from router import ConsistentRouter, NaiveRouter

REPLICA_LAG = 0.100       # steady-state replication lag: 100 ms
CATCHUP_TIMEOUT = 0.050   # max wait for a replica to catch up
QUERY_DELAY = 0.001       # simulated per-query RTT
DURATION = 1.2            # seconds per configuration
SHARED_KEYS = [f"item:{i}" for i in range(20)]


def run_workload(router, stop_flag):
    def writer():
        i = 0
        while not stop_flag.is_set():
            router.primary.write(SHARED_KEYS[i % len(SHARED_KEYS)], i)
            i += 1
            time.sleep(0.002)

    def reader(rid):
        s = router.session()
        i = 0
        while not stop_flag.is_set():
            if i % 10 == 9:  # own write then read: RYW-critical
                router.write(s, f"user:{rid}", i)
                router.read(s, f"user:{rid}")
            else:
                router.read(s, SHARED_KEYS[i % len(SHARED_KEYS)])
            i += 1

    threads = [threading.Thread(target=writer)]
    threads += [threading.Thread(target=reader, args=(r,)) for r in range(4)]
    for t in threads:
        t.start()
    time.sleep(DURATION)
    stop_flag.set()
    for t in threads:
        t.join()


def bench(label, router, stop_flag):
    run_workload(router, stop_flag)
    snap = router.stats.snapshot()
    print(f"{label:<28} {snap['fallback_ratio']:>11.1%} "
          f"{snap['avg_read_latency']*1000:>11.2f}ms "
          f"{snap['total_reads']:>8} {snap['violations']:>10}")
    return snap


def main():
    print(f"replica lag={REPLICA_LAG*1000:.0f}ms  "
          f"catchup_timeout={CATCHUP_TIMEOUT*1000:.0f}ms  "
          f"query_rtt={QUERY_DELAY*1000:.0f}ms  duration={DURATION}s\n")
    print(f"{'configuration':<28} {'fallback%':>11} {'avg latency':>12} "
          f"{'reads':>8} {'violations':>10}")

    # Baseline 1: always read primary (threshold impossible to satisfy).
    primary = Primary(query_delay=QUERY_DELAY)
    replicas = [Replica(primary, REPLICA_LAG, "r0", QUERY_DELAY)]
    bench("always-primary (baseline)",
          ConsistentRouter(primary, replicas, lag_threshold=-1),
          threading.Event())

    # Baseline 2: naive replica reads, no consistency control.
    primary = Primary(query_delay=QUERY_DELAY)
    replicas = [Replica(primary, REPLICA_LAG, "r0", QUERY_DELAY)]
    bench("naive-replica (no control)",
          NaiveRouter(primary, replicas),
          threading.Event())

    # Consistent router across lag thresholds.
    for thr_ms in (0, 25, 50, 100, 150, 200, 500):
        primary = Primary(query_delay=QUERY_DELAY)
        replicas = [Replica(primary, REPLICA_LAG, "r0", QUERY_DELAY)]
        bench(f"consistent thr={thr_ms}ms",
              ConsistentRouter(primary, replicas,
                               lag_threshold=thr_ms / 1000,
                               catchup_timeout=CATCHUP_TIMEOUT),
              threading.Event())
        for r in replicas:
            r.stop()


if __name__ == "__main__":
    main()
