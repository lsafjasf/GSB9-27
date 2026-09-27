#!/usr/bin/env python3
"""Print before/after metric samples for every required scenario.

Also asserts every invariant while printing, so the file doubles as an
executable specification. Run: python3 demo_scenarios.py
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from metrics.store import MetricsStore  # noqa: E402

WINDOW = 3600
HOUR = 1_700_000_000 - (1_700_000_000 % WINDOW)
T0 = HOUR + 20 * 60
T1 = HOUR + 40 * 60
T2 = HOUR + WINDOW + 5 * 60


def row(label, store, ts):
    return (label, store.value("requests"),
            store.window_value("requests", ts),
            store.monotones.get("bytes_sent", 0))


def print_table(rows):
    print("%-34s %-10s %-12s %-12s" % ("phase", "total", "this hour", "bytes_sent"))
    for label, total, window, mono in rows:
        print("%-34s %-10s %-12s %-12s" % (label, total, window, mono))


def scenario_normal_restart(path):
    print("\n### 1. Normal restart in the middle of a window")
    rows = []
    store = MetricsStore(path, WINDOW, clock=lambda: T0)
    store.incr("requests", 5)
    store.add_monotone("bytes_sent", 50)
    store.flush()
    rows.append(row("before restart @00:20", store, T0))

    store = MetricsStore(path, WINDOW, clock=lambda: T1)
    rows.append(row("immediately after restart @00:40", store, T1))
    store.incr("requests", 3)
    store.add_monotone("bytes_sent", 30)
    store.flush()
    rows.append(row("after +3 requests @00:40", store, T1))
    print_table(rows)
    assert rows[2][2] == rows[0][2] + 3 == 8
    assert rows[1][2] == 5 and rows[2][1] == 8 and rows[2][3] == 80
    print("ASSERT window(after) == window(before) + post-restart increments: OK (8 == 5 + 3)")


def scenario_two_restarts(path):
    print("\n### 2. Two consecutive restarts, second one crosses a window")
    rows = []
    store = MetricsStore(path, WINDOW, clock=lambda: T0)
    store.incr("requests", 3)
    store.flush()
    rows.append(row("run1 @00:20", store, T0))

    store = MetricsStore(path, WINDOW, clock=lambda: T1)
    store.incr("requests", 4)
    store.flush()
    rows.append(row("run2 @00:40 (same hour)", store, T1))

    store = MetricsStore(path, WINDOW, clock=lambda: T2)
    store.incr("requests", 7)
    store.flush()
    rows.append(row("run3 @01:05 (new hour)", store, T2))
    print_table(rows)
    series = dict(store.series("requests", T0, T2))
    assert list(series.values()) == [7, 7]
    assert store.value("requests") == 14
    print("ASSERT hourly series == [7, 7] (hour0 = 3+4, hour1 = 7): OK")


def scenario_sigkill(path):
    print("\n### 3. SIGKILL (periodic flush, real processes)")
    interval = "0.05"

    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "app.py"), "daemon", path,
         "--interval", interval],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 5
    pre = None
    while time.time() < deadline:
        if os.path.exists(path):
            try:
                pre = MetricsStore(path, WINDOW)
                if pre.value("requests") >= 5:
                    break
            except Exception:
                pass
        time.sleep(0.02)
    assert pre is not None and pre.value("requests") >= 5
    before = row("before SIGKILL (last flush)", pre, time.time())
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=5)

    revived = MetricsStore(path, WINDOW)
    after_kill = row("reopened after SIGKILL", revived, time.time())
    assert revived.value("requests") >= before[1]

    proc2 = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "app.py"), "daemon", path,
         "--interval", interval],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.4)
    proc2.send_signal(signal.SIGTERM)
    proc2.wait(timeout=5)
    final = MetricsStore(path, WINDOW)
    resumed = row("resumed process (SIGTERM flush)", final, time.time())
    print_table([before, after_kill, resumed])
    assert final.value("requests") > revived.value("requests")
    assert final.monotones["bytes_sent"] >= revived.monotones["bytes_sent"]
    print("ASSERT no corruption, no rollback, resumed total > reopened total: OK")


def scenario_stale(path):
    print("\n### 4. Stale on-disk file")
    store = MetricsStore(path, WINDOW, clock=lambda: T0)
    store.incr("requests", 9)
    store.flush()
    old = T0 - 12 * WINDOW
    os.utime(path, (old, old))
    print("file written at %s, pretending clock is %s (age 12h)" % (old, T0))
    try:
        MetricsStore(path, WINDOW, max_age_seconds=6 * WINDOW,
                     clock=lambda: T0)
    except Exception as exc:
        print("rejected with: %s: %s" % (type(exc).__name__, exc))
    accepted = MetricsStore(path, WINDOW, clock=lambda: T0)
    print("without max_age_seconds: loads, requests = %s (history preserved)"
          % accepted.value("requests"))
    assert accepted.value("requests") == 9


def main():
    with tempfile.TemporaryDirectory() as tmp:
        scenario_normal_restart(os.path.join(tmp, "normal.json"))
        scenario_two_restarts(os.path.join(tmp, "twice.json"))
        scenario_sigkill(os.path.join(tmp, "kill.json"))
        scenario_stale(os.path.join(tmp, "stale.json"))
    print("\nALL SCENARIO ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
