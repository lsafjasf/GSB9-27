"""Regression tests for crash-safe session state.

Run:  python3 -m unittest -v test_session_state.py
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest

from session_state import (STATE_VERSION, STEPS, EffectGateway,
                           SessionEngine, StateCorruptError,
                           StateVersionError, SimulatedCrash)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "runner.py")

SESSION = "s-1"
ACTION = "a-1"


def run_process(engine, workdir, crash_after=None, kill=False):
    cmd = [sys.executable, RUNNER, engine, workdir, SESSION, ACTION]
    if crash_after:
        cmd += ["--crash-after", crash_after]
    if kill:
        cmd.append("--kill")
    return subprocess.run(cmd, capture_output=True, text=True)


def effects_by_kind(workdir):
    counts = {}
    for rec in EffectGateway(workdir).applied_effects():
        counts[rec["kind"]] = counts.get(rec["kind"], 0) + 1
    return counts


class ReproTest(unittest.TestCase):
    """The original bug: killed after the side effect, before the state
    write; on restart the buggy engine repeats the external effect."""

    def test_buggy_engine_duplicates_side_effects(self):
        with tempfile.TemporaryDirectory() as workdir:
            proc = run_process("buggy", workdir,
                               crash_after="notify", kill=True)
            self.assertEqual(proc.returncode, -signal.SIGKILL)

            proc = run_process("buggy", workdir)  # "resume" after restart
            self.assertEqual(proc.returncode, 0, proc.stderr)

            counts = effects_by_kind(workdir)
            # BUG reproduced: the notification was sent twice.
            self.assertEqual(counts.get("notify"), 2)


class FixedEngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def assert_at_most_once(self):
        gateway = EffectGateway(self.workdir)
        keys = [rec["key"] for rec in gateway.applied_effects()]
        self.assertEqual(len(keys), len(set(keys)),
                         "duplicate external side effect")
        for step in STEPS:
            self.assertIn(f"{SESSION}:{ACTION}:{step}", keys)

    def test_first_execution(self):
        engine = SessionEngine(self.workdir, SESSION)
        state = engine.run_action(ACTION)
        self.assertEqual(state["actions"][ACTION]["status"], "done")
        self.assertEqual(effects_by_kind(self.workdir),
                         {"notify": 1, "deduct_quota": 1})

    def test_crash_after_effect_then_restart_sigkill(self):
        proc = run_process("fixed", self.workdir,
                           crash_after="notify", kill=True)
        self.assertEqual(proc.returncode, -signal.SIGKILL)

        proc = run_process("fixed", self.workdir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_at_most_once()
        self.assertEqual(effects_by_kind(self.workdir),
                         {"notify": 1, "deduct_quota": 1})

    def test_repeated_recovery_crashes_at_every_step(self):
        # Kill at the worst point of step 1, restart and get killed at the
        # worst point of step 2, restart again and finish.
        for step in STEPS:
            proc = run_process("fixed", self.workdir,
                               crash_after=step, kill=True)
            self.assertEqual(proc.returncode, -signal.SIGKILL)
        proc = run_process("fixed", self.workdir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_at_most_once()

    def test_in_process_simulated_crash(self):
        engine = SessionEngine(self.workdir, SESSION)
        with self.assertRaises(SimulatedCrash):
            engine.run_action(
                ACTION,
                crash_hook=lambda step: (_ for _ in ()).throw(
                    SimulatedCrash(step)))
        SessionEngine(self.workdir, SESSION).run_action(ACTION)  # resume
        self.assert_at_most_once()

    def test_corrupt_state_file_recovers_from_journal(self):
        proc = run_process("fixed", self.workdir,
                           crash_after="deduct_quota", kill=True)
        self.assertEqual(proc.returncode, -signal.SIGKILL)
        self.assertEqual(effects_by_kind(self.workdir),
                         {"notify": 1, "deduct_quota": 1})

        # Damage the state file: truncated, half-written JSON.
        state_path = os.path.join(self.workdir, f"state-{SESSION}.json")
        with open(state_path, "r+", encoding="utf-8") as fh:
            fh.truncate(os.path.getsize(state_path) // 2)

        # Engine must rebuild from the effect journal, not re-execute.
        SessionEngine(self.workdir, SESSION).run_action(ACTION)
        self.assert_at_most_once()
        with open(state_path, encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(state["actions"][ACTION]["status"], "done")

    def test_malformed_state_shape_rebuilds_from_journal(self):
        # Valid JSON that is structurally incomplete used to sail past the
        # shape check and then blow up as a KeyError at action-record write
        # time.  Each variant must instead be treated as corrupt and rebuilt
        # from the journal, without re-executing already-applied effects.
        malformed_states = [
            {},
            {"version": STATE_VERSION, "session_id": SESSION},
            {"version": STATE_VERSION, "session_id": SESSION,
             "actions": None},
            {"version": STATE_VERSION, "session_id": SESSION,
             "actions": []},
            {"version": STATE_VERSION, "session_id": SESSION,
             "actions": {ACTION: {"status": "pending"}}},
            {"version": STATE_VERSION, "session_id": SESSION,
             "actions": {ACTION: {"status": "weird", "steps": {}}}},
            {"version": STATE_VERSION, "session_id": SESSION,
             "actions": {ACTION: {"status": "pending",
                                  "steps": {"notify": {"status": "done"}}}}},
        ]
        state_path = os.path.join(self.workdir, f"state-{SESSION}.json")
        for malformed in malformed_states:
            with self.subTest(malformed=malformed):
                self._tmp.cleanup()
                self._tmp = tempfile.TemporaryDirectory()
                workdir = self._tmp.name
                # Journal already shows the notify effect for this action.
                EffectGateway(workdir).apply(
                    f"{SESSION}:{ACTION}:notify", "notify",
                    {"session": SESSION, "action": ACTION})
                with open(os.path.join(
                        workdir, f"state-{SESSION}.json"),
                        "w", encoding="utf-8") as fh:
                    json.dump(malformed, fh)

                state = SessionEngine(workdir, SESSION).run_action(ACTION)
                self.assertEqual(
                    effects_by_kind(workdir),
                    {"notify": 1, "deduct_quota": 1})
                self.assertEqual(
                    state["actions"][ACTION]["status"], "done")

    def test_corrupt_rebuild_recovers_all_session_actions(self):
        # Two actions complete, a third interrupted after its first effect;
        # corrupting the state and resuming action 3 must rebuild records for
        # *all* actions of the session, not only the requested one.
        engine = SessionEngine(self.workdir, SESSION)
        engine.run_action("a-1")
        engine.run_action("a-2")
        with self.assertRaises(SimulatedCrash):
            engine.run_action(
                "a-3",
                crash_hook=lambda step: (_ for _ in ()).throw(
                    SimulatedCrash(step)))

        state_path = os.path.join(self.workdir, f"state-{SESSION}.json")
        with open(state_path, "r+", encoding="utf-8") as fh:
            fh.truncate(os.path.getsize(state_path) // 2)

        state = SessionEngine(self.workdir, SESSION).run_action("a-3")

        # Sibling actions survived the rebuild; no effect was re-executed.
        self.assertEqual(
            {aid: action["status"]
             for aid, action in state["actions"].items()},
            {"a-1": "done", "a-2": "done", "a-3": "done"})
        self.assertEqual(effects_by_kind(self.workdir),
                         {"notify": 3, "deduct_quota": 3})
        with open(state_path, encoding="utf-8") as fh:
            persisted = json.load(fh)
        self.assertEqual(set(persisted["actions"]), {"a-1", "a-2", "a-3"})

    def test_version_mismatch_refuses_to_run(self):
        state_path = os.path.join(self.workdir, f"state-{SESSION}.json")
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump({"version": STATE_VERSION + 1,
                       "session_id": SESSION, "actions": {}}, fh)
        with self.assertRaises(StateVersionError):
            SessionEngine(self.workdir, SESSION).run_action(ACTION)
        self.assertEqual(effects_by_kind(self.workdir), {})


if __name__ == "__main__":
    unittest.main()
