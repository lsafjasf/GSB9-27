"""Regression suite for crash-safe session state.

Crash model: SimulatedKill is a BaseException raised at an exact point,
which is what SIGKILL looks like to business code - no except-Exception
handler runs, no finally-based cleanup in the runner runs, and the next
"process" is a brand-new runner instance over the same state directory.
"""
import json
import os
import tempfile
import unittest

from runner import BuggySessionRunner, SessionRunner, SideEffectRecorder, SimulatedKill
from session_state import (
    CorruptStateError,
    StateStore,
    StateVersionError,
    STATE_FILENAME,
)

SESSION = "session-1"
ACTION = "notify-and-charge"


def kill_at(point):
    def hook(p):
        if p == point:
            raise SimulatedKill(f"killed at {p}")
    return hook


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = os.path.join(self.tmp.name, "state")
        self.effects_path = os.path.join(self.tmp.name, "effects.log")
        self.recorder = SideEffectRecorder(self.effects_path)

    def make_runner(self, cls=SessionRunner, crash_hook=None):
        return cls(self.state_dir, SESSION, self.recorder, crash_hook=crash_hook)


class ReproTest(Base):
    def test_buggy_runner_duplicates_side_effect_after_kill(self):
        """REPRO: kill after the effect fired but before state was written.

        On restart the buggy runner cannot tell the effect already happened,
        so it fires it again: duplicate notification, duplicate charge.
        """
        runner = self.make_runner(BuggySessionRunner, kill_at("after_effect"))
        with self.assertRaises(SimulatedKill):
            runner.run_action(ACTION, "charge:user42")

        # "Restart the process" and resume the same session.
        resumed = self.make_runner(BuggySessionRunner)
        resumed.run_action(ACTION, "charge:user42")

        self.assertEqual(
            self.recorder.count(), 2,
            "bug reproduced: the same action produced two external side effects",
        )

    def test_fixed_runner_same_scenario_no_duplicate(self):
        """Same kill point as the repro, fixed runner: at most one effect."""
        runner = self.make_runner(SessionRunner, kill_at("after_effect"))
        with self.assertRaises(SimulatedKill):
            runner.run_action(ACTION, "charge:user42")

        resumed = self.make_runner(SessionRunner)
        result = resumed.run_action(ACTION, "charge:user42")

        self.assertEqual(result, "skipped: effect already committed")
        self.assertEqual(self.recorder.count(), 1)


class RecoveryTest(Base):
    def test_first_execution_fires_once_and_persists(self):
        result = self.make_runner().run_action(ACTION, "charge:user42")
        self.assertEqual(result, "executed")
        self.assertEqual(self.recorder.count(), 1)
        self.assertTrue(os.path.exists(os.path.join(self.state_dir, STATE_FILENAME)))

        # Plain re-run (no crash) is also idempotent.
        again = self.make_runner().run_action(ACTION, "charge:user42")
        self.assertEqual(again, "skipped: effect already committed")
        self.assertEqual(self.recorder.count(), 1)

    def test_crash_at_every_point_never_duplicates(self):
        expectations = {
            # commit never happened -> recovery fires it once
            "before_effect_commit": 1,
            # committed but not fired -> recovery skips; at-most-once allows 0
            "after_effect_commit": 0,
            # fired after commit -> recovery skips
            "after_effect": 1,
        }
        for point, expected in expectations.items():
            with self.subTest(crash_point=point), tempfile.TemporaryDirectory() as d:
                rec = SideEffectRecorder(os.path.join(d, "fx.log"))
                sdir = os.path.join(d, "state")
                r1 = SessionRunner(sdir, SESSION, rec, crash_hook=kill_at(point))
                with self.assertRaises(SimulatedKill):
                    r1.run_action(ACTION, "charge:user42")
                r2 = SessionRunner(sdir, SESSION, rec)
                r2.run_action(ACTION, "charge:user42")
                self.assertLessEqual(rec.count(), 1, "must never duplicate")
                self.assertEqual(rec.count(), expected)

    def test_multi_step_resume_skips_completed_steps(self):
        log = []

        def step_a():
            log.append("a")

        def step_b():
            log.append("b")

        def hook(point):
            if point == "before_effect_commit":
                raise SimulatedKill("killed after steps, before commit")

        r1 = self.make_runner(crash_hook=hook)
        with self.assertRaises(SimulatedKill):
            r1.run_action(ACTION, "charge:user42", steps=[step_a, step_b])
        self.assertEqual(log, ["a", "b"])

        r2 = self.make_runner()
        r2.run_action(ACTION, "charge:user42", steps=[step_a, step_b])
        self.assertEqual(log, ["a", "b"], "completed steps must not re-run")
        self.assertEqual(self.recorder.count(), 1)


class CorruptionTest(Base):
    def _state_path(self):
        return os.path.join(self.state_dir, STATE_FILENAME)

    def test_truncated_state_fails_closed(self):
        """Half a state file (torn write) -> CorruptStateError, no re-run."""
        self.make_runner().run_action(ACTION, "charge:user42")
        self.assertEqual(self.recorder.count(), 1)

        with open(self._state_path(), "rb") as fh:
            good = fh.read()
        with open(self._state_path(), "wb") as fh:
            fh.write(good[: len(good) // 2])  # simulate half-written state

        with self.assertRaises(CorruptStateError):
            self.make_runner()
        self.assertEqual(self.recorder.count(), 1, "corruption must not replay effects")
        quarantines = [f for f in os.listdir(self.state_dir) if ".corrupt-" in f]
        self.assertEqual(len(quarantines), 1, "bad file should be quarantined")

    def test_checksum_mismatch_detected(self):
        self.make_runner().run_action(ACTION, "charge:user42")
        with open(self._state_path(), "rb") as fh:
            doc = json.loads(fh.read())
        doc["payload"]["effects"][ACTION] = "tampered"
        with open(self._state_path(), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)  # checksum now stale

        with self.assertRaises(CorruptStateError):
            self.make_runner()
        self.assertEqual(self.recorder.count(), 1)

    def test_version_mismatch_refuses_to_run(self):
        self.make_runner().run_action(ACTION, "charge:user42")
        store = StateStore(self.state_dir, SESSION)
        payload = store.load()
        payload["version"] = 999
        store.save(payload)  # valid checksum, wrong version

        with self.assertRaises(StateVersionError):
            self.make_runner()
        self.assertEqual(self.recorder.count(), 1, "version mismatch must not re-run")

    def test_orphan_tmp_file_ignored(self):
        """A leftover temp file from a killed save() must not affect load."""
        self.make_runner().run_action(ACTION, "charge:user42")
        with open(os.path.join(self.state_dir, ".state-orphan.tmp"), "w") as fh:
            fh.write('{"payload": {"version": 1, "garbage')

        runner = self.make_runner()  # loads the real state.json, ignores tmp
        result = runner.run_action(ACTION, "charge:user42")
        self.assertEqual(result, "skipped: effect already committed")
        self.assertEqual(self.recorder.count(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
