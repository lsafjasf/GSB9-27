"""Demo: concurrent workload against the pool, printing reuse stats.

Run:  python3 demo.py
"""

import socket
import threading
import time

from pool import ConnectionPool


def main():
    peers = []                          # keep peer ends alive

    def factory():                      # stand-in for a TCP connect
        ours, peer = socket.socketpair()
        peers.append(peer)
        return ours

    pool = ConnectionPool(factory, max_size=8, max_idle=8,
                          idle_timeout=0.2, reap_interval=0.05)

    def worker(n):
        for _ in range(n):
            with pool.acquire(timeout=5.0) as conn:
                conn.raw.fileno()       # pretend to do work

    threads = [threading.Thread(target=worker, args=(250,)) for _ in range(16)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - start

    time.sleep(0.4)                     # let the reaper collect idle conns
    snap = pool.snapshot()
    print(f"borrows (借出总次数)        : {snap['borrows']}")
    print(f"created (建连次数)          : {snap['created']}")
    print(f"reused  (复用次数)          : {snap['reused']}")
    print(f"reuse rate (复用率)         : {snap['reuse_rate']:.2%}")
    print(f"reaped  (空闲回收)          : {snap['reaped']}")
    print(f"validation failures (校验)  : {snap['validation_failures']}")
    print(f"idle / in_use / total       : "
          f"{snap['idle']} / {snap['in_use']} / {snap['total']}")
    print(f"elapsed                     : {elapsed:.3f}s")
    pool.close()


if __name__ == "__main__":
    main()
