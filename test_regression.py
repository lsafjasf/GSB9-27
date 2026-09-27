#!/usr/bin/env python3
"""Regression tests for the durable metrics store.

Covers:
  * normal restart with mid-window conservation (assertion required)
  * SIGKILL restart with a periodically flushing real process
  * two consecutive restarts, including across a window boundary
  * monotone values never move backwards (assertion required)
  * truncated / corrupted / garbage state files are detected and handled
    by explicit policy; backup recovery works; silent zero-start impossible
  * stale state files are rejected by age policy
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from metrics.store import (  # noqa: E402
    CorruptStateFileError,
    MetricsStore,
    StaleStateFileError,
)

WINDOW = 3600
HOUR = 1_700_000_000 - (1_700_000_000 % WINDOW)
T0 = HOUR + 20 * 60
T1 = HOUR + 40 * 60
T2 = HOUR + WINDOW + 5 * 60  # next hour, 5 minutes in


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, ts, **kwargs):
        return MetricsStore(self.path, WINDOW, clock=lambda: ts, **kwargs)


class TestMidWindowRestart(StoreTestBase):
    def test_window_aggregate_is_before_plus_after(self):
        store = self.store(T0)
        store.incr("requests", 5)
        store.flush()
        before = store.window_value("requests", T0)

        restarted = self.store(T1)  # same fixed window, 20 minutes later
        restarted.incr("requests", 3)
        restarted.flush()
        after = restarted.window_value("requests", T1)

        # THE conservation assertion: a mid-window restart must be transparent.
        self.assertEqual(after, before + 3)
        self.assertEqual(after, 8)
        self.assertEqual(restarted.value("requests"), 8)

    def test_normal_restart_keeps_previous_windows(self):
        store = self.store(T0)
        store.incr("requests", 5)
        store.flush()
        next_hour = self.store(T2)
        next_hour.incr("requests", 2)
        next_hour.flush()
        series = dict(next_hour.series("requests", T0, T2))
        self.assertEqual(series[T0 - T0 % WINDOW], 5)
        self.assertEqual(series[T2 - T2 % WINDOW], 2)
        # zero-filled buckets mean no missing points / no "fault" in the curve
        self.assertEqual(len(series), 2)


class TestTwoConsecutiveRestarts(StoreTestBase):
    def test_restart_twice_conserves_each_window(self):
        first = self.store(T0)
        first.incr("requests", 1)
        first.incr("requests", 2)
        first.flush()

        second = self.store(T1)
        second.incr("requests", 4)
        second.add_monotone("bytes_sent", 40)
        second.flush()

        third = self.store(T2)  # new window
        third.incr("requests", 7)
        third.add_monotone("bytes_sent", 70)
        third.flush()

        self.assertEqual(third.window_value("requests", T0), 7)
        self.assertEqual(third.window_value("requests", T2), 7)
        self.assertEqual(third.value("requests"), 14)
        self.assertEqual(third.monotones["bytes_sent"], 110)
        series = dict(third.series("requests", T0, T2))
        self.assertEqual(list(series.values()), [7, 7])

    def test_restart_twice_real_processes(self):
        out1 = self._run_once(T0, 3, 30)
        out2 = self._run_once(T1, 4, 10)
        out3 = self._run_once(T2, 2, 5)
        self.assertEqual(out1["recovered_from"], "fresh")
        self.assertEqual(out2["recovered_from"], "primary")
        self.assertEqual(out3["recovered_from"], "primary")
        self.assertEqual(out3["counters"]["requests"], 9)
        self.assertEqual(out3["monotones"]["bytes_sent"], 45)
        windows = out3["windows"]["requests"]
        self.assertEqual(windows[str(T0 // WINDOW)], 7)
        self.assertEqual(windows[str(T2 // WINDOW)], 2)

    def _run_once(self, ts, requests, bytes_sent):
        env = dict(os.environ, METRICS_FIXED_TS=str(ts))
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "app.py"), "once", self.path,
             "--incr", "requests=%d" % requests,
             "--mono", "bytes_sent=%d" % bytes_sent],
            env=env, capture_output=True, text=True, check=True,
        )
        return json.loads(proc.stdout)


class TestMonotone(StoreTestBase):
    def test_monotone_never_decreases_across_restarts(self):
        observed = []
        store = self.store(T0)
        store.add_monotone("bytes_sent", 100)
        store.flush()
        observed.append(store.monotones["bytes_sent"])

        for ts, amount in ((T1, 40), (T2, 0), (T2 + 60, 60)):
            store = self.store(ts)
            store.add_monotone("bytes_sent", amount)
            store.flush()
            observed.append(store.monotones["bytes_sent"])

        self.assertEqual(observed, [100, 140, 140, 200])
        # THE monotonicity assertion, pair-wise:
        for earlier, later in zip(observed, observed[1:]):
            self.assertGreaterEqual(later, earlier)

    def test_negative_monotone_update_rejected(self):
        store = self.store(T0)
        store.add_monotone("bytes_sent", 10)
        with self.assertRaises(ValueError):
            store.add_monotone("bytes_sent", -1)


class TestCorruption(StoreTestBase):
    def _seed(self, ts=T0, amount=10):
        store = self.store(ts)
        store.incr("requests", amount)
        store.flush()
        return store

    def test_truncated_file_is_detected(self):
        self._seed()
        with open(self.path, "r+b") as handle:
            handle.truncate(3)
        with self.assertRaises(CorruptStateFileError):
            self.store(T1, on_corrupt="fail")
        with self.assertRaises(CorruptStateFileError):
            self.store(T1, on_corrupt="recover")  # no backup available

    def test_garbage_file_is_detected(self):
        self._seed()
        with open(self.path, "wb") as handle:
            handle.write(b"\xff\xfe not json at all")
        with self.assertRaises(CorruptStateFileError):
            self.store(T1)

    def test_tampered_checksum_is_detected(self):
        self._seed()
        with open(self.path, encoding="utf-8") as handle:
            envelope = json.loads(handle.read())
        envelope["payload"]["counters"]["requests"] = 99999
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(envelope, handle)
        with self.assertRaises(CorruptStateFileError):
            self.store(T1)

    def test_recovered_from_backup_after_bad_primary(self):
        store = self._seed(amount=10)
        # a second flush rotates the first state to .bak with value 10 and
        # gives the primary value 13
        store = self.store(T0 + 10)
        store.incr("requests", 3)
        store.flush()
        with open(self.path, "wb") as handle:
            handle.write(b"{torn")
        recovered = self.store(T1, on_corrupt="recover")
        self.assertEqual(recovered.recovered_from, "backup")
        self.assertEqual(recovered.value("requests"), 10)

    def test_reset_policy_is_explicit_and_quarantines(self):
        self._seed()
        with open(self.path, "wb") as handle:
            handle.write(b"xxx")
        store = self.store(T1, on_corrupt="reset")
        self.assertEqual(store.recovered_from, "reset")
        self.assertEqual(store.value("requests"), 0)
        quarantined = os.listdir(os.path.join(self.tmp.name, "quarantine"))
        self.assertTrue(any(name.endswith(".corrupt") for name in quarantined))

    def test_no_silent_zero_on_empty_file(self):
        self._seed()
        with open(self.path, "wb"):
            pass
        with self.assertRaises(CorruptStateFileError):
            self.store(T1)  # default on_corrupt='fail'

    def test_flush_is_atomic_no_tmp_left_behind(self):
        store = self._seed()
        store.flush()
        leftovers = [name for name in os.listdir(self.tmp.name)
                     if name.startswith("state.json.tmp")]
        self.assertEqual(leftovers, [])
        self.assertTrue(os.path.exists(self.path + ".bak"))


class TestStaleState(StoreTestBase):
    def test_stale_file_rejected_then_explicit_accept(self):
        store = self.store(T0)
        store.incr("requests", 2)
        store.flush()
        stale_mtime = T0 - 5 * WINDOW
        os.utime(self.path, (stale_mtime, stale_mtime))
        if os.path.exists(self.path + ".bak"):
            os.utime(self.path + ".bak", (stale_mtime, stale_mtime))
        with self.assertRaises(StaleStateFileError):
            self.store(T0, max_age_seconds=3 * WINDOW)
        accepted = self.store(T0)  # no age policy -> usable history
        self.assertEqual(accepted.value("requests"), 2)


class TestKillMinusNine(unittest.TestCase):
    """Real process: SIGKILL with periodic flush must lose at most the tail,
    never corrupt the file, and aggregates must not move backwards."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _start_daemon(self, interval):
        proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "app.py"), "daemon", self.path,
             "--interval", str(interval)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return proc

    def _wait_for_state(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(self.path):
                try:
                    store = MetricsStore(self.path, WINDOW)
                    return store
                except CorruptStateFileError:
                    pass
            time.sleep(0.02)
        self.fail("daemon never produced a valid state file")

    def test_sigkill_resumes_without_corruption_or_regression(self):
        interval = 0.05
        proc = self._start_daemon(interval)
        store = self._wait_for_state()
        last_total = store.value("requests")
        last_window = store.window_value("requests")
        last_mono = store.monotones["bytes_sent"]
        self.assertGreaterEqual(last_total, 1)

        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)

        # The file on disk must still be valid (atomic flush).
        revived = MetricsStore(self.path, WINDOW)
        self.assertIn(revived.recovered_from, ("primary", "backup"))
        # Tail loss is bounded to what was not yet flushed; nothing on disk
        # may have been destroyed.
        self.assertGreaterEqual(revived.value("requests"), last_total)
        self.assertGreaterEqual(revived.window_value("requests"), last_window)
        self.assertGreaterEqual(revived.monotones["bytes_sent"], last_mono)

        # Process resumes and the curve continues upwards.
        proc2 = self._start_daemon(interval)
        time.sleep(0.5)
        proc2.send_signal(signal.SIGTERM)
        proc2.wait(timeout=5)
        final = MetricsStore(self.path, WINDOW)
        self.assertGreater(final.value("requests"), revived.value("requests"))
        self.assertGreaterEqual(
            final.window_value("requests"),
            revived.window_value("requests"),
        )
        self.assertGreater(final.monotones["bytes_sent"],
                           revived.monotones["bytes_sent"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
