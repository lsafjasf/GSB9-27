#!/usr/bin/env python3
"""Evidence: long-running supervisor does not leak processes/zombies.

Restarts a fast-crashing child many times and samples, from /proc:
  - zombie children of this process
  - total live children of this process
"""

import os
import sys
import threading
import time

from supervisor import Supervisor

PY = sys.executable
SELF = os.getpid()


def proc_counts():
    zombies = children = 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as fh:
                stat = fh.read()
        except OSError:
            continue
        fields = stat[stat.rfind(")") + 2:].split()
        state, ppid = fields[0], int(fields[1])
        if ppid == SELF:
            children += 1
            if state == "Z":
                zombies += 1
    return zombies, children


def main():
    restarts = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    sup = Supervisor(
        [PY, "-c", "import sys; sys.exit(1)"],
        base_delay=0.001, max_delay=0.005,
        fast_crash_window=1.0, fast_crash_threshold=10**9,
        logger=lambda _msg: None,
    )
    thread = threading.Thread(target=sup.run, daemon=True)

    z0, c0 = proc_counts()
    print(f"before:  zombies={z0} children={c0}")
    thread.start()

    peak_z = peak_c = 0
    while sup.attempts < restarts:
        zombies, children = proc_counts()
        peak_z = max(peak_z, zombies)
        peak_c = max(peak_c, children)
        time.sleep(0.02)
    sup.stop()
    thread.join(timeout=10)
    time.sleep(0.2)

    z1, c1 = proc_counts()
    print(f"during:  peak zombies={peak_z} peak children={peak_c} "
          f"(<=1 live child at a time, never a zombie)")
    print(f"after:   zombies={z1} children={c1} attempts={sup.attempts}")
    ok = z0 == 0 and peak_z == 0 and z1 == 0 and c1 == 0
    verdict = (f"PASS - no zombie/process growth over {sup.attempts} restarts"
               if ok else "FAIL")
    print(f"RESULT:  {verdict}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
