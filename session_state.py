"""Crash-safe session state engine (fixed implementation).

Design notes
------------
Idempotency key
    Each (session, action, step) gets a *deterministic* idempotency key:
    ``<session_id>:<action_id>:<step>``.  The key is persisted in the state
    file as a write-ahead intent record *before* the side effect is executed.
    If the process is killed after the effect but before the completion
    record, recovery finds the intent, reuses the same key, and the effect
    gateway (which honors idempotency keys, like Stripe-style APIs) dedupes
    the retry.  Net result: at most one externally visible side effect.

Partial writes
    State is written atomically: serialize to a temp file in the same
    directory, fsync, os.replace(), then fsync the directory.  A crash can
    therefore only leave either the old or the new state -- never a torn
    file.  If the state file is nevertheless found corrupt (disk damage,
    manual truncation), the engine does NOT guess: it rebuilds the state for
    the requested action from the effect journal.  Because keys are
    deterministic, the journal can be queried for exactly the keys this
    action would have used; steps whose key is already applied are marked
    done, the rest stay pending.  No step is ever re-executed blindly.

Version mismatch
    The state file carries a "version" field.  A mismatch raises
    StateVersionError and the engine refuses to run -- no silent migration,
    no re-execution.
"""

import json
import os
import tempfile

STATE_VERSION = 1

# Steps of an action, executed in order.  Each produces one external effect.
STEPS = ("notify", "deduct_quota")


class StateCorruptError(Exception):
    """State file exists but is not valid JSON / not an object."""


class StateVersionError(Exception):
    """State file version does not match STATE_VERSION."""


class SimulatedCrash(BaseException):
    """In-process stand-in for SIGKILL (not catchable by except Exception)."""


class EffectGateway:
    """Simulates an external system that honors idempotency keys.

    Every call is logged to ``requests.log`` (attempt count); an effect is
    appended to ``effects.log`` only the first time its key is seen
    (externally visible side effects).
    """

    def __init__(self, workdir):
        self.requests_path = os.path.join(workdir, "requests.log")
        self.effects_path = os.path.join(workdir, "effects.log")

    def _read_log(self, path):
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def _append(self, path, record):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def apply(self, key, kind, payload):
        """Record the attempt; apply the effect only if key is new."""
        self._append(self.requests_path, {"key": key, "kind": kind})
        applied = {rec["key"] for rec in self._read_log(self.effects_path)}
        if key in applied:
            return False
        self._append(self.effects_path,
                     {"key": key, "kind": kind, "payload": payload})
        return True

    def applied_keys(self):
        return {rec["key"] for rec in self._read_log(self.effects_path)}

    def applied_effects(self):
        return self._read_log(self.effects_path)

    def request_count(self):
        return len(self._read_log(self.requests_path))


class SessionEngine:
    def __init__(self, workdir, session_id):
        self.workdir = workdir
        self.session_id = session_id
        self.state_path = os.path.join(workdir, f"state-{session_id}.json")
        self.gateway = EffectGateway(workdir)

    # -- state persistence -------------------------------------------------

    def _fresh_state(self):
        return {"version": STATE_VERSION,
                "session_id": self.session_id,
                "actions": {}}

    def _save(self, state):
        fd, tmp = tempfile.mkstemp(dir=self.workdir,
                                   prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.state_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        dir_fd = os.open(self.workdir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _load(self):
        """Load state, or None if no state file exists yet."""
        if not os.path.exists(self.state_path):
            return None
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise StateCorruptError(
                f"state file {self.state_path} is corrupt: {exc}") from exc
        if not isinstance(state, dict) or "version" not in state:
            raise StateCorruptError(
                f"state file {self.state_path} has unexpected shape")
        if state["version"] != STATE_VERSION:
            raise StateVersionError(
                f"state version {state['version']!r} != {STATE_VERSION}")
        return state

    def _rebuild_from_journal(self, action_id):
        """Rebuild state for one action from the effect journal.

        Safe because idempotency keys are deterministic: we can ask the
        gateway exactly which keys this action would have used.
        """
        state = self._fresh_state()
        steps = {}
        for step in STEPS:
            key = self._key(action_id, step)
            if key in self.gateway.applied_keys():
                steps[step] = {"status": "done", "idempotency_key": key}
        state["actions"][action_id] = {"status": "pending", "steps": steps}
        return state

    # -- action execution --------------------------------------------------

    def _key(self, action_id, step):
        return f"{self.session_id}:{action_id}:{step}"

    def run_action(self, action_id, crash_hook=None):
        """Run all steps of an action; safe to call again after a crash.

        crash_hook(step) is invoked after the side effect but before the
        completion record is written -- the worst possible crash point.
        """
        try:
            state = self._load()
        except StateCorruptError:
            state = self._rebuild_from_journal(action_id)
        if state is None:
            state = self._fresh_state()

        action = state["actions"].setdefault(
            action_id, {"status": "pending", "steps": {}})

        for step in STEPS:
            rec = action["steps"].get(step)
            if rec and rec["status"] == "done":
                continue
            if rec is None:
                # Write-ahead intent: persist the idempotency key BEFORE
                # performing the side effect.
                rec = {"status": "in_progress",
                       "idempotency_key": self._key(action_id, step)}
                action["steps"][step] = rec
                self._save(state)
            self.gateway.apply(rec["idempotency_key"], step,
                               {"session": self.session_id,
                                "action": action_id})
            if crash_hook is not None:
                crash_hook(step)
            rec["status"] = "done"
            self._save(state)

        action["status"] = "done"
        self._save(state)
        return state
