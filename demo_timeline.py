#!/usr/bin/env python3
"""Print restart timelines for the four required failure scenarios."""

import sys
import threading
import time

from supervisor import Supervisor

PY = sys.executable


def scenario(title, argv, stop_after_attempts=None, do_reset=False, **kw):
    print(f"\n=== {title} ===")
    kw.setdefault("base_delay", 0.2)
    kw.setdefault("max_delay", 1.0)
    kw.setdefault("fast_crash_window", 0.5)
    kw.setdefault("fast_crash_threshold", 3)
    sup = Supervisor(argv, **kw)
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()

    if stop_after_attempts is not None:
        while sup.attempts < stop_after_attempts:
            time.sleep(0.01)
        sup.stop()
    if do_reset:
        while not sup.circuit_open:
            time.sleep(0.01)
        time.sleep(0.5)  # circuit stays open: no new attempts
        print(f"    ... circuit open, attempts frozen at {sup.attempts} ...")
        sup.reset()
        time.sleep(1.5)
        sup.stop()
    thread.join(timeout=10)


def main():
    scenario("1. child exits immediately (crash loop -> circuit -> manual reset)",
             [PY, "-c", "import sys; sys.exit(1)"], do_reset=True)
    scenario("2. child runs for a while, then crashes (no circuit)",
             [PY, "-c", "import time,sys; time.sleep(0.8); sys.exit(1)"],
             stop_after_attempts=3)
    scenario("3. child is killed by a signal",
             [PY, "-c",
              "import os,signal,time; time.sleep(0.1);"
              " os.kill(os.getpid(), signal.SIGKILL)"],
             stop_after_attempts=2)
    scenario("4. executable does not exist (start failure)",
             ["/nonexistent/not-a-real-binary"], do_reset=True)
    scenario("5. child exits normally (code 0): no restart",
             [PY, "-c", "print('work done')"], stop_after_attempts=None)
    while threading.active_count() > 1:
        time.sleep(0.05)


if __name__ == "__main__":
    main()
