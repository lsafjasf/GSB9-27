"""Throughput benchmark for the lock-free overwrite ring buffer."""

import threading
import time

from ringbuffer import RingBuffer, OverrunError


def bench_solo_write(cap, n, payload):
    buf = RingBuffer(cap)
    t0 = time.perf_counter()
    for _ in range(n):
        buf.write(payload)
    dt = time.perf_counter() - t0
    return n / dt


def bench_solo_read(cap, n, payload):
    buf = RingBuffer(cap)
    for i in range(cap):
        buf.write(payload)
    r = buf.reader()
    t0 = time.perf_counter()
    for _ in range(n):
        r.read()
    dt = time.perf_counter() - t0
    return n / dt


def bench_concurrent(cap, duration, n_readers, payload):
    buf = RingBuffer(cap)
    stop = threading.Event()
    written = 0
    read_counts = [0] * n_readers
    overrun_counts = [0] * n_readers
    lock = threading.Lock()

    def writer():
        nonlocal written
        i = 0
        while not stop.is_set():
            i += 1
            buf.write((i, payload, i))
        with lock:
            written = i

    def reader(k):
        r = buf.reader()
        cnt = 0
        ovr = 0
        while not stop.is_set():
            try:
                item = r.read()
            except OverrunError:
                ovr += 1
                continue
            if item is None:
                continue
            cnt += 1
        read_counts[k] = cnt
        overrun_counts[k] = ovr

    wt = threading.Thread(target=writer)
    rts = [threading.Thread(target=reader, args=(k,)) for k in range(n_readers)]
    t0 = time.perf_counter()
    wt.start()
    for t in rts:
        t.start()
    time.sleep(duration)
    stop.set()
    wt.join()
    for t in rts:
        t.join()
    dt = time.perf_counter() - t0
    return written / dt, [c / dt for c in read_counts], sum(overrun_counts), dt


def main():
    small = b"x" * 32
    big = b"y" * 1024
    print("== Single-threaded (no contention), 10M ops ==")
    for name, p in (("32B", small), ("1KB", big)):
        w = bench_solo_write(1024, 10_000_000, p)
        rr = bench_solo_read(1024, 10_000_000, p)
        print(f"  payload={name:>4}  write {w:>12,.0f} rec/s   read {rr:>12,.0f} rec/s")

    print()
    print("== Concurrent, readers keep up (no overrun): 2 s, cap=2^22, 32B ==")
    for n_readers in (1, 2, 4):
        wr, rrs, ovr, dt = bench_concurrent(1 << 22, 2.0, n_readers, small)
        per = "  ".join(f"{x:,.0f}" for x in rrs)
        agg = sum(rrs)
        print(f"  readers={n_readers}  writer {wr:>12,.0f} rec/s | "
              f"per-reader: {per} | aggregate {agg:,.0f} rec/s | overruns {ovr}")

    print()
    print("== Concurrent, readers far behind (overrun churn): 3 s, cap=1024, 32B ==")
    for n_readers in (1, 2, 4):
        wr, rrs, ovr, dt = bench_concurrent(1024, 3.0, n_readers, small)
        per = "  ".join(f"{x:,.0f}" for x in rrs)
        print(f"  readers={n_readers}  writer {wr:>12,.0f} rec/s | "
              f"per-reader rec/s: {per} | overruns {ovr}")


if __name__ == "__main__":
    main()
