#!/usr/bin/env python3
"""Reproduce the post-restart curve faults and show the fixed behavior.

Runs entirely in one process but simulates "restarts" by constructing a new
store object over the same on-disk file (the buggy and fixed stores only
keep state in memory + on disk, so this is equivalent to a process restart
for these code paths). The SIGKILL path is exercised separately by
test_regression.py with real processes.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from metrics.naive_store import NaiveStore
from metrics.store import (
    CorruptStateFileError,
    MetricsStore,
)

WINDOW = 3600
# A fixed hour: t0 at 20 minutes past the hour; restart at 40 minutes past.
HOUR = 1_700_000_000 - (1_700_000_000 % WINDOW)
T0 = HOUR + 20 * 60
T1 = HOUR + 40 * 60


def scenario_mid_window_restart(tmpdir):
    print("== Scenario 1: restart lands in the middle of a window ==")
    expected = 0
    rows = []
    for label, factory in (
        ("naive", lambda p: NaiveStore(p, WINDOW, clock=lambda: T0)),
        ("fixed", lambda p: MetricsStore(p, WINDOW, clock=lambda: T0)),
    ):
        path = os.path.join(tmpdir, label + ".json")
        store = factory(path)
        for _ in range(5 if label == "fixed" else 5):
            store.incr("requests")
        if label == "fixed":
            store.add_monotone("bytes", 100)
        store.flush()
        before_window = store.window_value("requests", T0)
        before_total = store.value("requests")

        # --- simulated restart, clock advanced to T1 (same window) ---
        store2 = NaiveStore(path, WINDOW, clock=lambda: T1) if label == "naive" \
            else MetricsStore(path, WINDOW, clock=lambda: T1)
        store2.incr("requests", 3)
        if label == "fixed":
            store2.add_monotone("bytes", 40)
        store2.flush()
        after_window = store2.window_value("requests", T1)
        after_total = store2.value("requests")
        rows.append((label, before_total, before_window, after_total, after_window))

    expected_window = 8
    print("%-6s %-14s %-14s %-14s %-14s" %
          ("impl", "total before", "window before", "total after", "window after"))
    for label, bt, bw, at, aw in rows:
        print("%-6s %-14s %-14s %-14s %-14s" % (label, bt, bw, at, aw))
    naive = rows[0]
    fixed = rows[1]
    print("conservation assertion: window_after == window_before + post_restart_incr")
    print("  naive: %s == 5 + 3 -> %s (BUG: %s)" %
          (naive[4], naive[4] == expected_window, "FAULT" if naive[4] != expected_window else "ok"))
    assert naive[4] != expected_window, "naive bug disappeared"
    print("  fixed: %s == 5 + 3 -> %s" %
          (fixed[4], fixed[4] == expected_window))
    assert fixed[4] == expected_window, "fixed store breaks conservation"
    print()


def scenario_monotone(tmpdir):
    print("== Scenario 2: monotone series must never go backwards ==")
    path = os.path.join(tmpdir, "mono.json")
    store = MetricsStore(path, WINDOW, clock=lambda: T0)
    store.add_monotone("bytes_sent", 100)
    store.flush()
    before = store.monotones["bytes_sent"]
    MetricsStore(path, WINDOW, clock=lambda: T1).close()  # restart, no writes
    after = MetricsStore(path, WINDOW, clock=lambda: T1).monotones["bytes_sent"]
    print("  monotone before restart: %s" % before)
    print("  monotone after  restart: %s" % after)
    assert after >= before, "monotone regressed"
    print("  assertion after >= before: OK")
    print()


def scenario_corruption(tmpdir):
    print("== Scenario 3: truncated state file must not reset counters silently ==")
    path = os.path.join(tmpdir, "corrupt.json")
    store = MetricsStore(path, WINDOW, clock=lambda: T0)
    store.incr("requests", 10)
    store.flush()

    # Simulate a torn write / accidental truncation.
    with open(path, "r+b") as handle:
        handle.truncate(7)

    try:
        MetricsStore(path, WINDOW, on_corrupt="fail", clock=lambda: T1)
    except CorruptStateFileError as exc:
        print("  default policy raises: %s" % exc)
    else:
        raise AssertionError("corruption was not detected")

    store_reset = MetricsStore(path, WINDOW, on_corrupt="reset", clock=lambda: T1)
    print("  reset policy: recovered_from=%r (explicit opt-in, file quarantined)"
          % store_reset.recovered_from)
    assert store_reset.recovered_from == "reset"

    # Same experiment against the naive store: it silently starts at zero.
    npath = os.path.join(tmpdir, "naive_corrupt.json")
    naive = NaiveStore(npath, WINDOW, clock=lambda: T0)
    naive.incr("requests", 10)
    naive.flush()
    with open(npath, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    naive2 = NaiveStore(npath, WINDOW, clock=lambda: T1)
    print("  naive value after corrupt load: %s (BUG: silent zero)" % naive2.value("requests"))
    assert naive2.value("requests") == 0
    print()


def scenario_stale(tmpdir):
    print("== Scenario 4: stale state file ==")
    path = os.path.join(tmpdir, "stale.json")
    store = MetricsStore(path, WINDOW, clock=lambda: T0)
    store.incr("requests", 2)
    store.flush()
    old = T0 - 10 * WINDOW
    os.utime(path, (old, old))
    try:
        MetricsStore(path, WINDOW, max_age_seconds=3 * WINDOW,
                     clock=lambda: T0, on_corrupt="fail")
    except Exception as exc:
        print("  stale file rejected: %s" % exc)
    else:
        raise AssertionError("staleness not detected")
    accepted = MetricsStore(path, WINDOW, max_age_seconds=None,
                            clock=lambda: T0)
    assert accepted.value("requests") == 2
    print("  without an age limit the file still loads with value 2")
    print()


def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        scenario_mid_window_restart(tmpdir)
        scenario_monotone(tmpdir)
        scenario_corruption(tmpdir)
        scenario_stale(tmpdir)
    print("ALL REPRODUCTION CHECKS PASSED")


if __name__ == "__main__":
    main()
