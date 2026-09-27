"""Cost benchmark: fallback ratio vs. overall read latency, by lag threshold.

Modelled node latencies (typical for a loaded primary vs. a local replica):
    primary read: 4.0 ms     replica read: 1.0 ms

Workload: concurrent sessions; each iteration does 1 write + 4 reads with a
small think time, so reads land both inside and outside the replication window.

Run:  python3 bench.py
"""

import threading

from consistent_read import Metrics, Primary, Replica, Router

PRIMARY_COST_MS = 4.0
REPLICA_COST_MS = 1.0

WRITER_SESSIONS = 8           # sessions doing write -> read-own-write
READER_SESSIONS = 16          # pure-read sessions (reads dominate, as in real apps)
READS_PER_ROUND = 6           # reads per round for pure readers
THINK_S = 0.004               # think time between rounds
MIN_DURATION_S = 0.4          # each config runs at least this long...


def run_workload(replica_lag, threshold, guarded=True):
    import time

    primary = Primary()
    replicas = [Replica(primary, lag=replica_lag), Replica(primary, lag=replica_lag)]
    metrics = Metrics()
    router = Router(primary, replicas, lag_threshold=threshold, guarded=guarded, metrics=metrics)

    # Seed a shared key and let it replicate, so pure reads exist in the mix.
    seed = router.session()
    seed.write("shared", "v")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not all(r.applied_lsn >= 1 for r in replicas):
        time.sleep(0.002)

    # Run to steady state: long enough for the replication-lag sawtooth to
    # dominate (>= 2x the configured lag), so the threshold policy is what
    # shapes the fallback ratio, not start-up transients.
    duration = max(MIN_DURATION_S, 2.0 * replica_lag)
    barrier = threading.Barrier(WRITER_SESSIONS + READER_SESSIONS)
    deadline = time.monotonic() + duration

    def writer(tid):
        s = router.session()
        key = f"k{tid}"
        barrier.wait()
        i = 0
        while time.monotonic() < deadline:
            i += 1
            s.write(key, i)
            s.read(key)                    # RYW-sensitive read
            s.read("shared")
            time.sleep(THINK_S)

    def reader():
        s = router.session()
        barrier.wait()
        while time.monotonic() < deadline:
            for _ in range(READS_PER_ROUND):
                s.read("shared")           # ordinary reads
            time.sleep(THINK_S)

    threads = ([threading.Thread(target=writer, args=(i,)) for i in range(WRITER_SESSIONS)]
               + [threading.Thread(target=reader) for _ in range(READER_SESSIONS)])
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for r in replicas:
        r.stop()

    snap = metrics.snapshot()
    total = snap["total_reads"]
    avg_ms = (snap["fallback_reads"] * PRIMARY_COST_MS + snap["replica_reads"] * REPLICA_COST_MS) / total
    return snap, avg_ms


def main():
    lags = [0.0, 0.02, 0.1, 0.5, 2.0]           # replica lag: 0 .. far beyond thresholds
    thresholds = [0.01, 0.05, 0.2, 1.0]         # lag thresholds (s)

    print(f"modelled latency: primary={PRIMARY_COST_MS}ms replica={REPLICA_COST_MS}ms")
    print(f"workload: {WRITER_SESSIONS} writer + {READER_SESSIONS} reader sessions, "
          f"steady-state duration >= max({MIN_DURATION_S}s, 2x lag)\n")

    header = f"{'replica lag':>12} | {'threshold':>9} | {'fallback%':>9} | {'avg read ms':>11} | {'violations':>10}"
    print(header)
    print("-" * len(header))
    for lag in lags:
        for th in thresholds:
            snap, avg_ms = run_workload(lag, th)
            print(f"{lag*1000:>10.0f}ms | {th*1000:>7.0f}ms | {snap['fallback_ratio']*100:>8.1f}% | "
                  f"{avg_ms:>10.2f}ms | {snap['violations']:>10}")
        print("-" * len(header))

    # Baseline: naive routing (always replica) -> fast but inconsistent.
    snap, avg_ms = run_workload(0.1, 0.05, guarded=False)
    print(f"\nnaive (always replica, lag=100ms): fallback={snap['fallback_ratio']*100:.1f}% "
          f"avg={avg_ms:.2f}ms violations={snap['violations']}  <- inconsistent!")
    # Baseline: always primary -> consistent but most expensive.
    print(f"always primary:                    fallback=100.0% avg={PRIMARY_COST_MS:.2f}ms violations=0")


if __name__ == "__main__":
    main()
