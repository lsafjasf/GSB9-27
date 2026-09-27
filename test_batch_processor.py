"""Regression tests for the checkpointed batch processor.

Run:  python3 -m unittest test_batch_processor -v
"""

import json
import os
import tempfile
import unittest
from collections import Counter

from batch_processor import (
    ALLOWED_TRANSITIONS,
    BatchProcessor,
    BatchStatus,
    IllegalTransitionError,
    RecordState,
)


class SimulatedKill(BaseException):
    """Stands in for SIGKILL / power loss: not caught by `except Exception`."""


class BatchProcessorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="batch-test-")
        self.state_path = os.path.join(self.tmpdir, "state.json")
        self.ids = [f"rec-{i}" for i in range(5)]
        self.effects = Counter()   # committed side effects per record
        self.calls = Counter()     # handler invocations per record

    def make_processor(self):
        return BatchProcessor("batch-1", self.ids, self.state_path)

    def ok_handler(self, rid):
        self.calls[rid] += 1
        self.effects[rid] += 1

    def failing_once_handler(self, fail_ids):
        def handler(rid):
            self.calls[rid] += 1
            if rid in fail_ids and self.calls[rid] == 1:
                raise RuntimeError(f"transient failure on {rid}")
            self.effects[rid] += 1
        return handler

    def always_failing_handler(self, rid):
        self.calls[rid] += 1
        raise RuntimeError(f"permanent failure on {rid}")

    def assert_effects_exactly_once(self, ids=None):
        for rid in (ids or self.ids):
            self.assertEqual(
                self.effects[rid], 1,
                f"{rid}: expected exactly one side effect, got {self.effects[rid]}",
            )

    # ---------------------------------------------------------- scenarios

    def test_first_record_fails_then_rerun_recovers(self):
        proc = self.make_processor()
        summary = proc.run(self.failing_once_handler({"rec-0"}))
        self.assertEqual((summary.succeeded, summary.failed, summary.skipped), (4, 1, 0))
        self.assertEqual(summary.status, BatchStatus.PARTIAL_SUCCESS)
        self.assertEqual(self.effects["rec-0"], 0)

        summary = self.make_processor().run(self.ok_handler)
        self.assertEqual(summary.status, BatchStatus.SUCCEEDED)
        self.assertEqual(self.calls["rec-0"], 2)          # retried once
        for rid in self.ids[1:]:
            self.assertEqual(self.calls[rid], 1, f"{rid} must not be reprocessed")
        self.assert_effects_exactly_once()

    def test_last_record_fails_then_rerun_recovers(self):
        proc = self.make_processor()
        summary = proc.run(self.failing_once_handler({"rec-4"}))
        self.assertEqual((summary.succeeded, summary.failed), (4, 1))
        self.assertEqual(summary.status, BatchStatus.PARTIAL_SUCCESS)

        summary = self.make_processor().run(self.ok_handler)
        self.assertEqual(summary.status, BatchStatus.SUCCEEDED)
        self.assertEqual(self.calls["rec-4"], 2)
        for rid in self.ids[:4]:
            self.assertEqual(self.calls[rid], 1)
        self.assert_effects_exactly_once()

    def test_all_records_fail(self):
        proc = self.make_processor()
        summary = proc.run(self.always_failing_handler)
        self.assertEqual((summary.succeeded, summary.failed, summary.skipped), (0, 5, 0))
        self.assertEqual(summary.status, BatchStatus.FAILED)
        self.assertEqual(sum(self.effects.values()), 0)

        summary = self.make_processor().run(self.always_failing_handler)  # rerun keeps retrying
        self.assertEqual(summary.status, BatchStatus.FAILED)
        for rid in self.ids:
            self.assertEqual(self.calls[rid], 2)
            rec = self.make_processor()._by_id[rid]
            self.assertEqual(rec.attempts, 2)
            self.assertEqual(rec.state, RecordState.FAILED)

    def test_killed_mid_processing_recovers_and_retries(self):
        killed = {"done": False}

        def killing_handler(rid):
            self.calls[rid] += 1
            if rid == "rec-2" and not killed["done"]:
                killed["done"] = True
                raise SimulatedKill()  # process dies before any side effect
            self.effects[rid] += 1

        with self.assertRaises(SimulatedKill):
            self.make_processor().run(killing_handler)

        # The checkpoint on disk shows rec-2 still in PROCESSING.
        with open(self.state_path) as fh:
            on_disk = {r["id"]: r["state"] for r in json.load(fh)["records"]}
        self.assertEqual(on_disk["rec-2"], RecordState.PROCESSING.value)
        self.assertEqual(on_disk["rec-0"], RecordState.SUCCEEDED.value)

        # Reloading recovers the interrupted record to FAILED (retryable).
        proc = self.make_processor()
        recovered = proc._by_id["rec-2"]
        self.assertEqual(recovered.state, RecordState.FAILED)
        self.assertIn("interrupted", recovered.error)

        summary = proc.run(self.ok_handler)
        self.assertEqual(summary.status, BatchStatus.SUCCEEDED)
        self.assertEqual(self.calls["rec-0"], 1)  # successes before the kill not redone
        self.assertEqual(self.calls["rec-1"], 1)
        self.assert_effects_exactly_once()

    def test_repeated_reruns_are_noops_after_completion(self):
        proc = self.make_processor()
        first = proc.run(self.ok_handler)
        self.assertEqual(first.status, BatchStatus.SUCCEEDED)
        calls_after_first = dict(self.calls)

        for _ in range(3):
            summary = self.make_processor().run(self.ok_handler)
            self.assertEqual(summary.status, BatchStatus.SUCCEEDED)
            self.assertEqual(summary.succeeded, 5)
        self.assertEqual(dict(self.calls), calls_after_first)  # handler never called again
        self.assert_effects_exactly_once()

    def test_rerun_only_processes_unfinished_records(self):
        proc = self.make_processor()
        proc.run(self.failing_once_handler({"rec-1", "rec-3"}))
        self.make_processor().run(self.ok_handler)
        self.assertEqual(self.calls["rec-1"], 2)
        self.assertEqual(self.calls["rec-3"], 2)
        for rid in ("rec-0", "rec-2", "rec-4"):
            self.assertEqual(self.calls[rid], 1)
        self.assert_effects_exactly_once()

    # ------------------------------------------------------- state machine

    def test_illegal_transitions_raise(self):
        proc = self.make_processor()
        rec = proc._by_id["rec-0"]

        with self.assertRaises(IllegalTransitionError):
            proc._transition(rec, RecordState.SUCCEEDED)   # PENDING -> SUCCEEDED
        with self.assertRaises(IllegalTransitionError):
            proc._transition(rec, RecordState.FAILED)      # PENDING -> FAILED

        proc._transition(rec, RecordState.PROCESSING)
        with self.assertRaises(IllegalTransitionError):
            proc._transition(rec, RecordState.PENDING)     # PROCESSING -> PENDING
        proc._transition(rec, RecordState.SUCCEEDED)
        with self.assertRaises(IllegalTransitionError):
            proc._transition(rec, RecordState.PROCESSING)  # SUCCEEDED is terminal

        rec2 = proc._by_id["rec-1"]
        proc.skip("rec-1")
        self.assertEqual(rec2.state, RecordState.SKIPPED)
        with self.assertRaises(IllegalTransitionError):
            proc._transition(rec2, RecordState.PROCESSING)  # SKIPPED is terminal

    def test_transition_table_is_complete_and_self_consistent(self):
        self.assertEqual(set(ALLOWED_TRANSITIONS), set(RecordState))
        for source, targets in ALLOWED_TRANSITIONS.items():
            for target in targets:
                self.assertIsInstance(target, RecordState)
                self.assertNotEqual(source, target)  # no self-loops

    def test_skipped_records_are_never_processed(self):
        proc = self.make_processor()
        proc.skip("rec-2")
        summary = proc.run(self.ok_handler)
        self.assertEqual((summary.succeeded, summary.failed, summary.skipped), (4, 0, 1))
        self.assertEqual(summary.status, BatchStatus.SUCCEEDED)
        self.assertEqual(self.calls["rec-2"], 0)
        self.assertEqual(self.effects["rec-2"], 0)

    def test_giving_up_on_failed_record_marks_it_skipped(self):
        proc = self.make_processor()
        proc.run(self.always_failing_handler)
        proc = self.make_processor()
        proc.skip("rec-0")
        summary = proc.summary()
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(summary.failed, 4)
        self.assertEqual(summary.status, BatchStatus.FAILED)  # no successes at all

    # ------------------------------------------------------------- summary

    def test_partial_success_status_and_counts(self):
        proc = self.make_processor()
        proc.skip("rec-4")
        summary = proc.run(self.failing_once_handler({"rec-1"}))
        self.assertEqual(summary.status, BatchStatus.PARTIAL_SUCCESS)
        self.assertEqual(summary.total, 5)
        self.assertEqual(summary.succeeded, 3)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(summary.pending, 0)
        self.assertIn("succeeded=3", str(summary))
        self.assertIn("failed=1", str(summary))
        self.assertIn("skipped=1", str(summary))

    def test_status_before_any_processing(self):
        proc = self.make_processor()
        self.assertEqual(proc.status(), BatchStatus.PENDING)
        self.assertEqual(proc.summary().pending, 5)

    # ---------------------------------------------------------- invariants

    def test_invariants_hold_after_every_persisted_state(self):
        proc = self.make_processor()
        proc.run(self.failing_once_handler({"rec-0", "rec-4"}))
        proc = self.make_processor()
        proc._check_invariants()  # raises AssertionError if broken
        proc.run(self.ok_handler)
        proc = self.make_processor()
        proc._check_invariants()
        for rec in proc.records:
            self.assertEqual(rec.state, RecordState.SUCCEEDED)
            self.assertGreaterEqual(rec.attempts, 1)
            self.assertIsNone(rec.error)

    def test_state_file_is_valid_json_after_each_run(self):
        proc = self.make_processor()
        proc.run(self.failing_once_handler({"rec-2"}))
        with open(self.state_path) as fh:
            data = json.load(fh)
        self.assertEqual(data["batch_id"], "batch-1")
        self.assertEqual(len(data["records"]), 5)
        states = {r["id"]: r["state"] for r in data["records"]}
        self.assertEqual(states["rec-2"], "failed")
        self.assertEqual(states["rec-0"], "succeeded")

    def test_mismatched_state_file_is_rejected(self):
        self.make_processor()
        with self.assertRaises(ValueError):
            BatchProcessor("batch-1", ["other-1"], self.state_path)
        with self.assertRaises(ValueError):
            BatchProcessor("different-batch", self.ids, self.state_path)


if __name__ == "__main__":
    unittest.main()
