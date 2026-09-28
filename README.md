# Crash-safe session state (idempotent side effects)

Fix for: after resuming from persisted state, already-executed actions were
re-done — duplicate notifications, duplicate quota deductions.

## Files

- `buggy_session.py` — original defective implementation (kept for the repro)
- `session_state.py` — fixed engine (`SessionEngine`) + `EffectGateway`
- `runner.py` — CLI to run one action in a subprocess (can be really SIGKILLed)
- `test_session_state.py` — repro + regression tests (stdlib `unittest` only)

## Root cause

The buggy engine generated a fresh idempotency key (`uuid4`) at execution
time and never persisted it. Killed after the side effect but before the
state write, recovery re-executed the step with a *new* key, so the effect
gateway could not dedupe it → duplicate external side effect.

## Fix

- **Idempotency key**: deterministic `session_id:action_id:step`, written to
  the state file as a *write-ahead intent* **before** the side effect runs.
  On recovery the intent is found, the same key is reused, and the gateway
  (which honors idempotency keys, Stripe-style) applies the effect at most
  once. Steps already marked `done` are skipped entirely.
- **Partial writes**: state is written atomically — temp file in the same
  directory, `fsync`, `os.replace`, `fsync` the directory — so a crash
  yields either the old or the new state, never a torn file. If the state
  file is nevertheless corrupt (disk damage, truncation, valid JSON with a
  broken envelope — missing `actions`, malformed action/step records), the
  engine does not guess: the whole envelope is shape-validated on load, and
  any structural failure is treated as corruption and rebuilt from the
  effect journal. Because keys are deterministic and prefixed with the
  session id, the journal is scanned for **every** action of the session,
  not just the requested one (plus the requested one even if it has no
  effects yet): applied steps are marked done, the rest stay pending.
  Rebuilding for one action therefore never erases sibling actions when the
  state is written back. An action with no journal trace has produced no
  effects, so re-running it is still safe. Nothing is re-executed blindly.
- **Version mismatch**: the state carries a `version` field; a mismatch
  raises `StateVersionError` and the engine refuses to run (no silent
  migration, no re-execution).

## Run

```sh
python3 -m unittest -v test_session_state.py
```

Manual repro (real SIGKILL at the worst crash point):

```sh
mkdir -p /tmp/demo
python3 runner.py buggy /tmp/demo s-1 a-1 --crash-after notify --kill  # killed
python3 runner.py buggy /tmp/demo s-1 a-1   # resumes; notify sent TWICE
cat /tmp/demo/effects.log

python3 runner.py fixed /tmp/demo2 s-1 a-1 --crash-after notify --kill # killed
python3 runner.py fixed /tmp/demo2 s-1 a-1  # resumes; each effect exactly once
```

## Test coverage

- `test_buggy_engine_duplicates_side_effects` — repro: notify applied 2×
- `test_first_execution` — clean run, each effect exactly once
- `test_crash_after_effect_then_restart_sigkill` — kill between effect and
  state write, restart, at-most-once asserted via effect counts
- `test_repeated_recovery_crashes_at_every_step` — killed at every step's
  worst point across successive restarts, still at-most-once
- `test_in_process_simulated_crash` — same scenario without a subprocess
- `test_corrupt_state_file_recovers_from_journal` — truncated state file,
  rebuilt from journal, no duplicate effects
- `test_malformed_state_shape_rebuilds_from_journal` — valid JSON with
  missing/broken action structure is rejected by shape validation and
  rebuilt from the journal (no KeyError at record-write time)
- `test_corrupt_rebuild_recovers_all_session_actions` — multiple actions in
  one session, corrupt state, resume one action: all actions rebuilt, no
  duplicates
- `test_version_mismatch_refuses_to_run` — `StateVersionError`, zero effects
