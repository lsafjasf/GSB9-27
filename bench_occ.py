"""Benchmark: throughput and conflict rate per scenario.

Run:  python3 bench_occ.py
"""

import threading
import time

from occ import OCCStore, RetryExhausted


def scenario(name, threads, txns_per_thread, make_body, **run_kwargs):
    store = OCCStore()
    exhausted = [0]

    def worker(tid):
        for i in range(txns_per_thread):
            try:
                store.run(make_body(tid, i), **run_kwargs)
            except RetryExhausted:
                exhausted[0] += 1

    workers = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
    start = time.perf_counter()
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    elapsed = time.perf_counter() - start

    stats = store.stats.snapshot()
    commits = stats["commits"]
    conflicts = stats["conflicts"]
    attempts = commits + conflicts
    rate = (conflicts / attempts * 100) if attempts else 0.0
    tps = commits / elapsed if elapsed else 0.0
    print(f"{name:<28} {threads:>4} {commits:>8} {conflicts:>9} "
          f"{rate:>6.1f}% {stats['senior_commits']:>7} {exhausted[0]:>9} "
          f"{elapsed:>7.3f} {tps:>10.0f}")
    return stats


def main():
    print(f"{'scenario':<28} {'thr':>4} {'commits':>8} {'conflicts':>9} "
          f"{'rate':>7} {'seniors':>7} {'exhausted':>9} {'time(s)':>7} {'txn/s':>10}")
    print("-" * 100)

    # 1. single transaction stream, no concurrency at all
    scenario("single-thread", 1, 3000,
             lambda t, i: (lambda tx: tx.put("counter", (tx.get("counter", 0) or 0) + 1)))

    # 2. concurrent but disjoint key spaces -> no conflicts
    scenario("disjoint-keys", 8, 500,
             lambda t, i: (lambda tx: tx.put(f"k{t}", (tx.get(f"k{t}", 0) or 0) + 1)))

    # 3. high-conflict hotspot: everyone increments the same key
    def hot_body(t, i):
        def body(tx):
            value = tx.get("hot", 0) or 0
            time.sleep(0.0002)  # simulate real work inside the txn
            tx.put("hot", value + 1)
        return body
    scenario("hotspot-counter", 8, 150, hot_body)

    # 4. same hotspot, retries capped low and fairness disabled -> exhaustion
    scenario("hotspot-exhaustion(max=2)", 8, 150, hot_body,
             max_retries=2, senior_after=10 ** 9)

    # 5. read-heavy workload (the OCC sweet spot): 95% reads, 5% writes
    def read_heavy(t, i):
        def body(tx):
            total = 0
            for k in range(20):
                total += tx.get(f"acct{k}", 0) or 0
            if i % 20 == 0:
                tx.put(f"acct{i % 20}", (tx.get(f"acct{i % 20}", 0) or 0) + 1)
            return total
        return body
    scenario("read-heavy-95/5", 8, 500, read_heavy)


if __name__ == "__main__":
    main()
