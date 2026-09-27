"""Action runner with at-most-once external side effects.

Idempotency model:
- Idempotency key = (session_id, action_id). The session id scopes the
  StateStore, the action id is the key in the persisted `effects` map.
- The fixed runner commits the key to durable state BEFORE firing the
  external side effect. A crash anywhere can therefore never replay the
  effect: worst case it is recorded-but-not-fired (at-most-once).
- The buggy runner (kept for the regression repro) fires the effect first
  and records state afterwards, leaving a kill window that duplicates the
  effect on recovery.
"""
from __future__ import annotations

import os

from session_state import StateStore


class SimulatedKill(BaseException):
    """Mimics SIGKILL: BaseException, so `except Exception` / finally-based
    cleanup in business code does not swallow it, exactly like a hard kill."""


class SideEffectRecorder:
    """Stand-in for an external system (notification sender, quota service).

    Each fire() appends one fsync'd line; count() survives process restarts,
    which is what the idempotency assertions check.
    """

    def __init__(self, path: str):
        self.path = path

    def fire(self, label: str) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(label + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def count(self) -> int:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return sum(1 for line in fh if line.strip())
        except FileNotFoundError:
            return 0


class SessionRunner:
    """Fixed runner: commit the idempotency key, THEN fire the effect."""

    def __init__(self, state_dir, session_id, recorder, crash_hook=None):
        self.store = StateStore(state_dir, session_id)
        self.recorder = recorder
        self._crash_hook = crash_hook or (lambda point: None)
        self.state = self.store.load()

    def _crash_point(self, point: str) -> None:
        self._crash_hook(point)

    def run_action(self, action_id: str, effect_label: str, steps=()) -> str:
        key = action_id
        if key in self.state["effects"]:
            return "skipped: effect already committed"

        for step in steps:
            name = getattr(step, "__name__", "step")
            step_key = f"{key}:{name}"
            if self.state["steps"].get(step_key) == "done":
                continue  # resume: don't redo completed local steps
            step()
            self.state["steps"][step_key] = "done"
            self.store.save(self.state)  # checkpoint after every step

        # 1. Durably commit the idempotency key BEFORE the side effect.
        self._crash_point("before_effect_commit")
        self.state["effects"][key] = "committed"
        self.store.save(self.state)
        self._crash_point("after_effect_commit")

        # 2. Only now fire the external side effect.
        self.recorder.fire(effect_label)
        self._crash_point("after_effect")
        return "executed"


class BuggySessionRunner(SessionRunner):
    """Original broken ordering, kept to reproduce the bug:

    fire the effect first, write state afterwards. A kill inside the window
    between the two makes the recovery run replay the effect.
    """

    def run_action(self, action_id: str, effect_label: str, steps=()) -> str:
        key = action_id
        if key in self.state["effects"]:
            return "skipped: effect already committed"
        self.recorder.fire(effect_label)          # side effect first
        self._crash_point("after_effect")          # <-- kill window
        self.state["effects"][key] = "committed"   # state written after
        self.store.save(self.state)
        return "executed"
