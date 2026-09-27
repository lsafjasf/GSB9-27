"""Throughput benchmark: lock-free data path, 1 writer x N readers."""

import threading
import time

from ring_buffer import OverwriteRingBuffer

TOTAL = 1_000_000
CAPACITY = 1024


def bench_writer_only():
    buf = OverwriteRingBuffer(CAPACITY)
    start = time.perf_counter()
    for i in range(TOTAL):
        buf.write(i)
    elapsed = time.perf_counter() - start
    return TOTAL / elapsed


def bench_with_readers(n_readers):
    buf = OverwriteRingBuffer(CAPACITY)
    writer_done = threading.Event()
    consumed = [0] * n_readers

    def reader_fn(idx):
        reader = buf.reader(start=0)
        count = 0
        while True:
            res = reader.poll()
            if res is None:
                if writer_done.is_set() and reader.cursor > buf.latest_seq():
                    break
                time.sleep(0)
                continue
            count += 1
        consumed[idx] = count

    readers = [threading.Thread(target=reader_fn, args=(i,))
               for i in range(n_readers)]
    for t in readers:
        t.start()
    start = time.perf_counter()
    for i in range(TOTAL):
        buf.write(i)
    elapsed = time.perf_counter() - start
    writer_done.set()
    for t in readers:
        t.join()
    return TOTAL / elapsed, consumed


def main():
    w = bench_writer_only()
    print("writer only            : %12.0f writes/s" % w)
    for n in (1, 2, 4):
        rate, consumed = bench_with_readers(n)
        print("writer + %d reader(s)   : %12.0f writes/s | reads delivered: %s"
              % (n, rate, consumed))


if __name__ == "__main__":
    main()
