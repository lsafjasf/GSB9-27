"""Demo: build real 2-thread and 3-thread deadlocks and print the report.

Run:  python3 demo.py
"""

import threading
import time

import deadlock_detector as dd


def make_deadlock(locks):
    n = len(locks)
    barrier = threading.Barrier(n)
    for i in range(n):
        first, second = locks[i], locks[(i + 1) % n]

        def worker(first=first, second=second):
            first.acquire()
            barrier.wait()
            second.acquire()  # never returns

        threading.Thread(target=worker, daemon=True).start()


def main():
    make_deadlock([dd.TrackedLock("order-lock"), dd.TrackedLock("account-lock")])
    make_deadlock([dd.TrackedLock("shard-1"), dd.TrackedLock("shard-2"),
                   dd.TrackedLock("shard-3")])

    # Also start a benign condition wait to show it is NOT flagged.
    cond = dd.TrackedCondition(name="job-queue")
    threading.Thread(
        target=lambda: (cond.acquire(), cond.wait(10), cond.release()),
        name="job-consumer", daemon=True,
    ).start()

    time.sleep(1.0)  # let everyone block
    print(dd.report())


if __name__ == "__main__":
    main()
