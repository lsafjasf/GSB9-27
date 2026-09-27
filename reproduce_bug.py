"""Reproduces the original bug and shows the fixed behavior.

Bug symptom being reproduced:
    A batch fails halfway. On rerun, the buggy implementation reprocesses
    records that already succeeded (duplicate side effects) and skips the
    record that failed (it is never retried).

Run:  python3 reproduce_bug.py
Exit code 0 if the fixed processor behaves correctly, 1 otherwise.
"""

import os
import tempfile
from collections import Counter

from batch_processor import BatchProcessor, BatchStatus, RecordState


# --------------------------------------------------------------------------
# The original (buggy) implementation, kept here only to reproduce the bug.
# It remembers which records FAILED (so it can skip them) but never records
# which records SUCCEEDED -- so a rerun reprocesses every success and skips
# the one record that actually needs a retry.
# --------------------------------------------------------------------------
class BuggyBatchProcessor:
    def __init__(self, batch_id, record_ids, state_path):
        import json

        self.record_ids = record_ids
        self.state_path = state_path
        if os.path.exists(state_path):
            with open(state_path) as fh:
                self.failed = set(json.load(fh)["failed"])
        else:
            self.failed = set()

    def run(self, handler):
        import json

        for rid in self.record_ids:
            if rid in self.failed:
                continue  # BUG: failed records are never retried
            try:
                handler(rid)  # BUG: already-succeeded records run again
            except Exception:
                self.failed.add(rid)
        with open(self.state_path, "w") as fh:
            json.dump({"failed": sorted(self.failed)}, fh)


def scenario(processor_factory, state_path, record_ids):
    """Run a batch where rec-2 fails once, then rerun after it 'recovers'."""
    effects = Counter()
    calls = Counter()

    def handler(rid):
        calls[rid] += 1
        if rid == "rec-2" and calls[rid] == 1:
            raise RuntimeError("transient failure")
        effects[rid] += 1  # side effect

    processor_factory(record_ids, state_path).run(handler)
    processor_factory(record_ids, state_path).run(handler)  # the rerun
    return effects, calls


def main():
    record_ids = [f"rec-{i}" for i in range(5)]
    tmpdir = tempfile.mkdtemp(prefix="batch-repro-")

    print("=== buggy implementation (reproduces the reported bug) ===")
    effects, calls = scenario(
        lambda ids, path: BuggyBatchProcessor("b", ids, path),
        os.path.join(tmpdir, "buggy.json"),
        record_ids,
    )
    duplicated = sorted(r for r, n in effects.items() if n > 1)
    never_retried = "rec-2" not in effects
    print(f"  side-effect counts: {dict(effects)}")
    print(f"  duplicated side effects on rerun: {duplicated or 'none'}")
    print(f"  failed record 'rec-2' retried:    {not never_retried}")
    assert duplicated and never_retried, "expected the buggy version to show both symptoms"
    print("  -> bug reproduced: successes reprocessed, failed record skipped\n")

    print("=== fixed implementation ===")
    effects, calls = scenario(
        lambda ids, path: BatchProcessor("b", ids, path),
        os.path.join(tmpdir, "fixed.json"),
        record_ids,
    )
    print(f"  side-effect counts: {dict(effects)}")
    processor = BatchProcessor("b", record_ids, os.path.join(tmpdir, "fixed.json"))
    summary = processor.summary()
    print(f"  final summary: {summary}")

    ok = True
    if any(n != 1 for n in effects.values()):
        print("  FAIL: a record produced a duplicate side effect")
        ok = False
    if summary.status is not BatchStatus.SUCCEEDED:
        print("  FAIL: batch did not converge to succeeded after retry")
        ok = False
    if processor.records[2].state is not RecordState.SUCCEEDED:
        print("  FAIL: the previously failed record was not retried")
        ok = False

    print("\nRESULT:", "OK - fixed implementation passes" if ok else "BROKEN")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
