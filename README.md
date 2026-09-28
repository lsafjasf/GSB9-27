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
  yields either the old or the new state, never a torn file. The state is
  also read through a **structural envelope check** — top-level fields
  (`version`, `session_id`, `actions`), per-action records, per-step
  records and idempotency keys are all validated; a legal-JSON file that
  is missing or has a malformed field is rejected as corrupt rather than
  crashing later with a `KeyError`. If the state file is nevertheless
  corrupt (disk damage, truncation, damaged envelope), the engine does not
  guess: it rebuilds the state from the effect journal for **every action
  of the session**, not just the requested one — rebuilding a single
  action would silently wipe the sibling actions' records when the
  rebuilt state is saved. The action set is taken from the journal
  itself; because keys are deterministic, the journal can be probed for
  exactly the keys each action would have used, applied steps are marked
  done, the rest stay pending. Nothing is re-executed blindly.
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
- `test_shape_damaged_envelope_triggers_rebuild` — legal JSON with
  missing/malformed envelope fields (no `actions`, no `steps`, missing or
  forged idempotency key) is treated as corrupt and rebuilt, no KeyError
- `test_rebuild_restores_all_actions_of_session` — with two actions in
  one session (one done, one killed mid-flight), journal rebuild restores
  both and the sibling's records survive the post-rebuild save
- `test_version_mismatch_refuses_to_run` — `StateVersionError`, zero effects
