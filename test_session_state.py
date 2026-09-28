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
                           SessionEngine, StateVersionError, SimulatedCrash)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "runner.py")

SESSION = "s-1"
ACTION = "a-1"
ACTION_2 = "a-2"


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

    def test_shape_damaged_envelope_triggers_rebuild(self):
        # Legal JSON with a structurally damaged envelope must be treated
        # as corrupt (and rebuilt), never crash later with a KeyError.
        variants = (
            ("missing_actions",
             lambda s: s.pop("actions")),
            ("missing_steps",
             lambda s: s["actions"][ACTION].pop("steps")),
            ("missing_idempotency_key",
             lambda s: s["actions"][ACTION]["steps"]["notify"].pop(
                 "idempotency_key")),
            ("forged_idempotency_key",
             lambda s: s["actions"][ACTION]["steps"]["notify"].update(
                 {"idempotency_key": "forged:key"})),
        )
        for name, damage in variants:
            with self.subTest(variant=name):
                with tempfile.TemporaryDirectory() as vdir:
                    proc = run_process("fixed", vdir,
                                       crash_after="notify", kill=True)
                    self.assertEqual(proc.returncode, -signal.SIGKILL)
                    state_path = os.path.join(
                        vdir, f"state-{SESSION}.json")
                    with open(state_path, encoding="utf-8") as fh:
                        saved = json.load(fh)
                    damage(saved)
                    with open(state_path, "w", encoding="utf-8") as fh:
                        json.dump(saved, fh)

                    state = SessionEngine(vdir, SESSION).run_action(ACTION)
                    self.assertEqual(
                        state["actions"][ACTION]["status"], "done")
                    counts = effects_by_kind(vdir)
                    self.assertEqual(
                        counts, {"notify": 1, "deduct_quota": 1})

    def test_rebuild_restores_all_actions_of_session(self):
        # One action finishes fully; another is killed mid-flight.  A
        # corrupt envelope must be rebuilt for the WHOLE session: the
        # sibling action's records must survive the post-rebuild save.
        proc = run_process("fixed", self.workdir,
                           crash_after="notify", kill=True)
        self.assertEqual(proc.returncode, -signal.SIGKILL)
        SessionEngine(self.workdir, SESSION).run_action(ACTION_2)
        self.assertEqual(effects_by_kind(self.workdir),
                         {"notify": 2, "deduct_quota": 1})

        state_path = os.path.join(self.workdir, f"state-{SESSION}.json")
        with open(state_path, "r+", encoding="utf-8") as fh:
            fh.truncate(os.path.getsize(state_path) // 2)

        state = SessionEngine(self.workdir, SESSION).run_action(ACTION)
        self.assertIn(ACTION, state["actions"])
        self.assertIn(ACTION_2, state["actions"])
        self.assertEqual(state["actions"][ACTION]["status"], "done")
        self.assertEqual(state["actions"][ACTION_2]["status"], "done")
        # The sibling action was already fully applied; resume redoes
        # nothing (no extra effects).
        SessionEngine(self.workdir, SESSION).run_action(ACTION_2)
        self.assertEqual(effects_by_kind(self.workdir),
                         {"notify": 2, "deduct_quota": 2})

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
