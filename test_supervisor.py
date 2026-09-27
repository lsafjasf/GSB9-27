#!/usr/bin/env python3
"""Stdlib-only tests for supervisor.Supervisor."""

import os
import signal
import sys
import threading
import time
import unittest

from supervisor import Supervisor

PY = sys.executable


def count_zombies(parent_pid):
    """Count zombie processes whose parent is parent_pid (Linux /proc)."""
    count = 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as fh:
                stat = fh.read()
        except OSError:
            continue
        rparen = stat.rfind(")")
        fields = stat[rparen + 2:].split()
        state, ppid = fields[0], int(fields[1])
        if state == "Z" and ppid == parent_pid:
            count += 1
    return count


def run_in_thread(sup):
    thread = threading.Thread(target=sup.run, daemon=True)
    thread.start()
    return thread


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class SupervisorTests(unittest.TestCase):
    def make(self, argv, **kw):
        kw.setdefault("base_delay", 0.01)
        kw.setdefault("max_delay", 0.05)
        kw.setdefault("fast_crash_window", 1.0)
        kw.setdefault("fast_crash_threshold", 3)
        kw.setdefault("logger", lambda _msg: None)
        return Supervisor(argv, **kw)

    def test_normal_exit_not_restarted(self):
        sup = self.make([PY, "-c", "pass"])
        last = sup.run()
        self.assertTrue(last.normal)
        self.assertEqual(sup.attempts, 1)
        self.assertEqual(sup.exits[0].returncode, 0)

    def test_immediate_exit_trips_circuit(self):
        sup = self.make([PY, "-c", "import sys; sys.exit(1)"])
        thread = run_in_thread(sup)
        self.assertTrue(wait_until(lambda: sup.circuit_open))
        time.sleep(0.2)  # give it a chance to (wrongly) restart
        self.assertEqual(sup.attempts, 3, "must stop restarting once circuit is open")
        self.assertEqual(sup.fast_crashes, 3)
        self.assertTrue(all(e.returncode == 1 for e in sup.exits))
        sup.stop()
        thread.join(timeout=5)

    def test_manual_reset_resumes_restarts(self):
        sup = self.make([PY, "-c", "import sys; sys.exit(1)"])
        thread = run_in_thread(sup)
        self.assertTrue(wait_until(lambda: sup.circuit_open))
        self.assertEqual(sup.attempts, 3)
        sup.reset()
        self.assertTrue(wait_until(lambda: sup.attempts > 3))
        self.assertFalse(sup.circuit_open)
        self.assertEqual(sup.fast_crashes, 0)
        sup.stop()
        thread.join(timeout=5)

    def test_slow_crash_does_not_trip_circuit(self):
        # Child lives longer than fast_crash_window -> counter resets.
        sup = self.make(
            [PY, "-c", "import time,sys; time.sleep(0.3); sys.exit(1)"],
            fast_crash_window=0.2,
        )
        thread = run_in_thread(sup)
        self.assertTrue(wait_until(lambda: sup.attempts >= 4))
        self.assertFalse(sup.circuit_open)
        self.assertEqual(sup.fast_crashes, 0)
        sup.stop()
        thread.join(timeout=5)

    def test_killed_child_records_signal(self):
        sup = self.make(
            [PY, "-c", "import os,signal; os.kill(os.getpid(), signal.SIGTERM)"])
        thread = run_in_thread(sup)
        self.assertTrue(wait_until(lambda: len(sup.exits) >= 1))
        self.assertEqual(sup.exits[0].sig, signal.SIGTERM)
        self.assertIsNone(sup.exits[0].returncode)
        self.assertFalse(sup.exits[0].normal)
        sup.stop()
        thread.join(timeout=5)

    def test_missing_executable_counts_as_fast_crash(self):
        sup = self.make(["/nonexistent/definitely-not-here-xyz"])
        thread = run_in_thread(sup)
        self.assertTrue(wait_until(lambda: sup.circuit_open))
        self.assertEqual(sup.attempts, 3)
        self.assertTrue(all(e.start_failed for e in sup.exits))
        self.assertIn("No such file", sup.exits[0].error)
        sup.stop()
        thread.join(timeout=5)

    def test_backoff_is_exponential_and_capped(self):
        delays = []
        sup = self.make(
            [PY, "-c", "import sys; sys.exit(1)"],
            base_delay=0.5,
            max_delay=2.0,
            fast_crash_threshold=100,
            sleeper=delays.append,
        )
        thread = run_in_thread(sup)
        self.assertTrue(wait_until(lambda: len(delays) >= 5))
        sup.stop()
        thread.join(timeout=5)
        self.assertEqual(delays[:5], [0.5, 1.0, 2.0, 2.0, 2.0])

    def test_no_zombies_after_many_restarts(self):
        sup = self.make(
            [PY, "-c", "import sys; sys.exit(1)"],
            fast_crash_threshold=10_000,
        )
        self.assertEqual(count_zombies(os.getpid()), 0)
        thread = run_in_thread(sup)
        self.assertTrue(wait_until(lambda: sup.attempts >= 50, timeout=60))
        sup.stop()
        thread.join(timeout=5)
        time.sleep(0.1)
        self.assertEqual(count_zombies(os.getpid()), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
