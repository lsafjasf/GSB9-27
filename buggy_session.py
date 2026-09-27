"""Original buggy implementation, kept only to reproduce the defect.

Defects:
  1. The idempotency key is generated at execution time (uuid4) and never
     persisted, so after a crash the retried step uses a NEW key and the
     gateway cannot dedupe it -> duplicate external side effect.
  2. State is written with a plain open()/write() -- a crash mid-write can
     leave a torn file.
"""

import json
import os
import uuid

from session_state import STEPS, EffectGateway


class BuggySessionEngine:
    def __init__(self, workdir, session_id):
        self.workdir = workdir
        self.session_id = session_id
        self.state_path = os.path.join(workdir, f"state-{session_id}.json")
        self.gateway = EffectGateway(workdir)

    def _load(self):
        if not os.path.exists(self.state_path):
            return {"actions": {}}
        with open(self.state_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, state):
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)

    def run_action(self, action_id, crash_hook=None):
        state = self._load()
        action = state["actions"].setdefault(action_id, {"steps": {}})
        for step in STEPS:
            if action["steps"].get(step, {}).get("status") == "done":
                continue
            # BUG: fresh key per attempt, never persisted before the effect.
            key = f"{self.session_id}:{action_id}:{step}:{uuid.uuid4().hex}"
            self.gateway.apply(key, step, {"session": self.session_id,
                                           "action": action_id})
            if crash_hook is not None:
                crash_hook(step)
            action["steps"][step] = {"status": "done"}
            self._save(state)
        return state
