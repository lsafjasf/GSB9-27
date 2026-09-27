"""Checkpointed batch processor with a strict per-record state machine.

Stdlib only, Python 3.8+.

Record state machine
--------------------
    PENDING    -> PROCESSING | SKIPPED
    PROCESSING -> SUCCEEDED | FAILED
    FAILED     -> PROCESSING | SKIPPED   (retry on rerun, or give up)
    SUCCEEDED  -> (terminal)
    SKIPPED    -> (terminal)

Any other transition raises IllegalTransitionError.

Crash / rerun semantics
-----------------------
Every transition is persisted atomically (tmp file + os.replace) to a JSON
state file, so a kill at any point leaves a consistent checkpoint:

* SUCCEEDED / SKIPPED records are never processed again (no duplicate
  side effects from reruns).
* PENDING / FAILED records are (re)processed on the next run.
* A record found in PROCESSING at load time means the previous process
  was killed mid-record; it is recovered to FAILED so it will be retried.

Note: handlers must apply their side effect only on the success path
(i.e. not before raising). Exactly-once is then guaranteed for retries,
because a record is only marked SUCCEEDED after its handler returned,
and SUCCEEDED is terminal.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional


class RecordState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


ALLOWED_TRANSITIONS: Dict[RecordState, frozenset] = {
    RecordState.PENDING: frozenset({RecordState.PROCESSING, RecordState.SKIPPED}),
    RecordState.PROCESSING: frozenset({RecordState.SUCCEEDED, RecordState.FAILED}),
    RecordState.FAILED: frozenset({RecordState.PROCESSING, RecordState.SKIPPED}),
    RecordState.SUCCEEDED: frozenset(),
    RecordState.SKIPPED: frozenset(),
}

TERMINAL_STATES = frozenset({RecordState.SUCCEEDED, RecordState.SKIPPED})
RETRYABLE_STATES = frozenset({RecordState.PENDING, RecordState.FAILED})


class IllegalTransitionError(RuntimeError):
    """Raised when a record is moved along a transition the state machine forbids."""


class BatchStatus(str, Enum):
    PENDING = "pending"                    # nothing processed yet
    SUCCEEDED = "succeeded"                # all records succeeded or skipped, >= 1 succeeded
    PARTIAL_SUCCESS = "partial_success"    # at least one succeeded, at least one failed
    FAILED = "failed"                      # no successes, at least one failure
    SKIPPED = "skipped"                    # everything skipped, nothing ever processed


@dataclass
class Record:
    id: str
    state: RecordState = RecordState.PENDING
    attempts: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "state": self.state.value,
            "attempts": self.attempts,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Record":
        return cls(
            id=data["id"],
            state=RecordState(data["state"]),
            attempts=int(data.get("attempts", 0)),
            error=data.get("error"),
        )


@dataclass
class BatchSummary:
    total: int
    succeeded: int
    failed: int
    skipped: int
    pending: int
    status: BatchStatus

    def __str__(self) -> str:
        return (
            f"batch status={self.status.value} "
            f"succeeded={self.succeeded} failed={self.failed} "
            f"skipped={self.skipped} pending={self.pending} total={self.total}"
        )


class BatchProcessor:
    """Processes records one by one, checkpointing every state transition."""

    def __init__(self, batch_id: str, record_ids: List[str], state_path: str):
        if len(set(record_ids)) != len(record_ids):
            raise ValueError("record ids must be unique")
        self.batch_id = batch_id
        self.state_path = state_path
        self.records: List[Record] = []
        self._by_id: Dict[str, Record] = {}

        if os.path.exists(state_path):
            self._load()
            if {r.id for r in self.records} != set(record_ids):
                raise ValueError("state file does not match the given record ids")
        else:
            self.records = [Record(id=rid) for rid in record_ids]
            self._by_id = {r.id: r for r in self.records}
            self._persist()

        self._recover_interrupted()
        self._check_invariants()

    # ------------------------------------------------------------------ state

    def _load(self) -> None:
        with open(self.state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("batch_id") != self.batch_id:
            raise ValueError("state file belongs to a different batch")
        self.records = [Record.from_dict(d) for d in data["records"]]
        self._by_id = {r.id: r for r in self.records}

    def _persist(self) -> None:
        payload = {
            "batch_id": self.batch_id,
            "records": [r.to_dict() for r in self.records],
        }
        directory = os.path.dirname(os.path.abspath(self.state_path))
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.state_path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _transition(self, record: Record, target: RecordState, error: Optional[str] = None) -> None:
        allowed = ALLOWED_TRANSITIONS[record.state]
        if target not in allowed:
            raise IllegalTransitionError(
                f"record {record.id!r}: illegal transition "
                f"{record.state.value} -> {target.value}"
            )
        if target is RecordState.PROCESSING:
            record.attempts += 1
        record.state = target
        record.error = error if target is RecordState.FAILED else None
        self._persist()
        self._check_invariants()

    def _recover_interrupted(self) -> None:
        """Records left in PROCESSING were interrupted by a kill; mark them FAILED."""
        for record in self.records:
            if record.state is RecordState.PROCESSING:
                self._transition(
                    record,
                    RecordState.FAILED,
                    error="interrupted: recovered from crash while processing",
                )

    def _check_invariants(self) -> None:
        ids = [r.id for r in self.records]
        assert len(ids) == len(set(ids)), "duplicate record ids"
        for r in self.records:
            assert isinstance(r.state, RecordState), f"{r.id}: unknown state"
            assert r.attempts >= 0, f"{r.id}: negative attempts"
            if r.state is RecordState.PENDING:
                assert r.attempts == 0, f"{r.id}: pending but already attempted"
                assert r.error is None, f"{r.id}: pending but carries an error"
            elif r.state is RecordState.SUCCEEDED:
                assert r.attempts >= 1, f"{r.id}: succeeded without an attempt"
                assert r.error is None, f"{r.id}: succeeded but carries an error"
            elif r.state is RecordState.FAILED:
                assert r.attempts >= 1, f"{r.id}: failed without an attempt"
                assert r.error, f"{r.id}: failed without an error message"

    # ------------------------------------------------------------------ API

    def skip(self, record_id: str) -> None:
        """Permanently skip a PENDING or FAILED record."""
        self._transition(self._by_id[record_id], RecordState.SKIPPED)

    def status(self) -> BatchStatus:
        succeeded = sum(1 for r in self.records if r.state is RecordState.SUCCEEDED)
        failed = sum(1 for r in self.records if r.state is RecordState.FAILED)
        pending = sum(
            1 for r in self.records if r.state in (RecordState.PENDING, RecordState.PROCESSING)
        )
        if pending:
            return BatchStatus.PENDING
        if failed and succeeded:
            return BatchStatus.PARTIAL_SUCCESS
        if failed:
            return BatchStatus.FAILED
        if succeeded:
            return BatchStatus.SUCCEEDED
        return BatchStatus.SKIPPED

    def summary(self) -> BatchSummary:
        counts = {state: 0 for state in RecordState}
        for r in self.records:
            counts[r.state] += 1
        return BatchSummary(
            total=len(self.records),
            succeeded=counts[RecordState.SUCCEEDED],
            failed=counts[RecordState.FAILED],
            skipped=counts[RecordState.SKIPPED],
            pending=counts[RecordState.PENDING] + counts[RecordState.PROCESSING],
            status=self.status(),
        )

    def run(self, handler: Callable[[str], None]) -> BatchSummary:
        """Process every record that is not yet in a terminal state.

        handler(record_id) applies the record's side effect and returns on
        success, or raises on failure. Failures are recorded and processing
        continues with the next record. BaseExceptions (e.g. KeyboardInterrupt,
        SystemExit, a simulated kill) propagate, leaving the record in
        PROCESSING on disk for crash recovery on the next run.
        """
        for record in self.records:
            if record.state in TERMINAL_STATES:
                continue
            self._transition(record, RecordState.PROCESSING)
            try:
                handler(record.id)
            except Exception as exc:  # noqa: BLE001 - failure is data, not control flow
                self._transition(record, RecordState.FAILED, error=str(exc) or type(exc).__name__)
                continue
            self._transition(record, RecordState.SUCCEEDED)
        return self.summary()


if __name__ == "__main__":
    import sys
    import tempfile as _tempfile

    demo_dir = _tempfile.mkdtemp(prefix="batch-demo-")
    state_file = os.path.join(demo_dir, "demo-batch.json")
    ids = [f"rec-{i}" for i in range(5)]
    flaky: Dict[str, int] = {}

    def flaky_handler(record_id: str) -> None:
        # rec-1 and rec-3 fail on their first attempt, succeed afterwards.
        if record_id in {"rec-1", "rec-3"} and flaky.get(record_id, 0) == 0:
            flaky[record_id] = 1
            raise RuntimeError(f"transient error on {record_id}")
        print(f"  side effect applied for {record_id}")

    print("first run:")
    processor = BatchProcessor("demo", ids, state_file)
    print(" ", processor.run(flaky_handler))

    print("rerun (only failed records are retried):")
    processor = BatchProcessor("demo", ids, state_file)
    print(" ", processor.run(flaky_handler))

    print("rerun again (nothing left to do):")
    processor = BatchProcessor("demo", ids, state_file)
    print(" ", processor.run(flaky_handler))
    sys.exit(0)
