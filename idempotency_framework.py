"""
Idempotency verification framework (Python 3, stdlib only).

Verifies whether an interface is truly idempotent under repeated execution:
  - sequential replay   : same request sent N times one after another
  - concurrent replay   : same request fired from N threads simultaneously
  - timeout-then-retry  : first attempt applies its side effect but the client
                          observes a timeout, then retries with the same key

For every pattern the framework collects three kinds of evidence:
  1. side-effect counts  (charges / written records / emitted notifications)
  2. return-value consistency across attempts
  3. state-transition trace (snapshot of observable state after each attempt)

Verdicts:
  TRULY_IDEMPOTENT                            side effects applied exactly once
                                              AND return values consistent
  RETURN_CONSISTENT_BUT_SIDE_EFFECTS_REPEATED returns look idempotent but the
                                              side effect happened more than once
  NOT_IDEMPOTENT                              neither holds
"""

from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

TRULY_IDEMPOTENT = "TRULY_IDEMPOTENT"
RETURN_CONSISTENT_BUT_SIDE_EFFECTS_REPEATED = (
    "RETURN_CONSISTENT_BUT_SIDE_EFFECTS_REPEATED"
)
NOT_IDEMPOTENT = "NOT_IDEMPOTENT"

_VERDICT_SEVERITY = {
    TRULY_IDEMPOTENT: 0,
    RETURN_CONSISTENT_BUT_SIDE_EFFECTS_REPEATED: 1,
    NOT_IDEMPOTENT: 2,
}


class SimulatedTimeout(Exception):
    """Raised by a target when the server applied the request but the
    response was lost (client-side timeout)."""


# ---------------------------------------------------------------------------
# Target contract
# ---------------------------------------------------------------------------
# A target under test must provide:
#   execute(request: dict) -> dict        perform the operation; may raise
#                                         SimulatedTimeout
#   snapshot() -> dict                    deep-copyable view of all observable
#                                         side effects, MUST contain the key
#                                         "side_effect_count" (int)
#   reset() -> None                       back to a clean state


# ---------------------------------------------------------------------------
# Evidence data structures
# ---------------------------------------------------------------------------

@dataclass
class Attempt:
    index: int
    outcome: str                      # "ok" | "timeout" | "error"
    response: Optional[Any] = None
    error: Optional[str] = None
    snapshot_after: Optional[dict] = None


@dataclass
class PatternResult:
    pattern: str
    attempts: List[Attempt] = field(default_factory=list)
    side_effect_count: int = 0
    distinct_responses: List[Any] = field(default_factory=list)
    return_consistent: bool = True
    state_trace: List[dict] = field(default_factory=list)
    verdict: str = NOT_IDEMPOTENT
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "verdict": self.verdict,
            "rationale": self.rationale,
            "evidence": {
                "side_effect_count": self.side_effect_count,
                "return_consistent": self.return_consistent,
                "distinct_responses": self.distinct_responses,
                "state_trace": self.state_trace,
                "attempts": [
                    {
                        "index": a.index,
                        "outcome": a.outcome,
                        "response": a.response,
                        "error": a.error,
                    }
                    for a in self.attempts
                ],
            },
        }


@dataclass
class Report:
    target_name: str
    request: dict
    patterns: List[PatternResult] = field(default_factory=list)
    overall_verdict: str = NOT_IDEMPOTENT

    def finalize(self) -> None:
        self.overall_verdict = max(
            (p.verdict for p in self.patterns),
            key=lambda v: _VERDICT_SEVERITY[v],
        )

    def to_dict(self) -> dict:
        return {
            "target": self.target_name,
            "request": self.request,
            "overall_verdict": self.overall_verdict,
            "patterns": [p.to_dict() for p in self.patterns],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Judgement
# ---------------------------------------------------------------------------

def judge(side_effect_count: int, return_consistent: bool) -> str:
    if side_effect_count == 1 and return_consistent:
        return TRULY_IDEMPOTENT
    if side_effect_count > 1 and return_consistent:
        return RETURN_CONSISTENT_BUT_SIDE_EFFECTS_REPEATED
    return NOT_IDEMPOTENT


def _rationale(verdict: str, side_effect_count: int,
               return_consistent: bool) -> str:
    if verdict == TRULY_IDEMPOTENT:
        return ("side effect applied exactly once "
                f"(count={side_effect_count}) and all successful attempts "
                "returned identical values")
    if verdict == RETURN_CONSISTENT_BUT_SIDE_EFFECTS_REPEATED:
        return (f"return values are consistent, but the side effect was "
                f"applied {side_effect_count} times: the interface only "
                "pretends to be idempotent")
    return (f"side effect applied {side_effect_count} times and return "
            f"consistency={return_consistent}: not idempotent")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class IdempotencyChecker:
    def __init__(self, target, request: Optional[dict] = None,
                 sequential_repeats: int = 3,
                 concurrent_workers: int = 5,
                 retry_delay_seconds: float = 0.05):
        self.target = target
        self.request = request or {
            "idempotency_key": "chk-0001",
            "action": "charge",
            "amount": 100,
            "currency": "CNY",
        }
        self.sequential_repeats = sequential_repeats
        self.concurrent_workers = concurrent_workers
        self.retry_delay_seconds = retry_delay_seconds

    # -- evidence helpers ---------------------------------------------------

    def _snapshot(self) -> dict:
        return copy.deepcopy(self.target.snapshot())

    @staticmethod
    def _side_effects(snapshot: dict) -> int:
        return int(snapshot["side_effect_count"])

    @staticmethod
    def _scalars(value: Any) -> List[Any]:
        """All scalar values contained in a nested dict/list structure."""
        found: List[Any] = []
        if isinstance(value, dict):
            for v in value.values():
                found.extend(IdempotencyChecker._scalars(v))
        elif isinstance(value, (list, tuple)):
            for item in value:
                found.extend(IdempotencyChecker._scalars(item))
        elif value is None or isinstance(value, (str, int, float, bool)):
            found.append(value)
        return found

    def _anchored_consistency(self, response: Any, attempts: List[Attempt],
                              baseline: dict, final: dict) -> bool:
        """Single observed response + lost (timed-out) response: decide
        consistency by anchoring state-linked response fields against the
        snapshot taken right after the FIRST side-effecting attempt.

        A response field is "state-linked" if its value shows up in the
        final state (e.g. a transaction id that landed in the ledger).
        If every state-linked field was already present right after the
        first side effect, the lost response must have been identical;
        otherwise the lost response differed and the target is not
        return-consistent."""
        base_count = self._side_effects(baseline)
        first_effect_snapshot = None
        for a in attempts:
            if (a.snapshot_after is not None
                    and self._side_effects(a.snapshot_after) > base_count):
                first_effect_snapshot = a.snapshot_after
                break
        if first_effect_snapshot is None:
            return True
        final_scalars = self._scalars(final)
        first_scalars = self._scalars(first_effect_snapshot)
        linked = [v for v in self._scalars(response) if v in final_scalars]
        if not linked:
            return True  # nothing to anchor on; cannot prove inconsistency
        return all(v in first_scalars for v in linked)

    def _analyze(self, pattern: str, attempts: List[Attempt],
                 baseline: dict) -> PatternResult:
        # take a FRESH snapshot: under concurrency the last attempt's
        # snapshot is not necessarily the final state
        final = self._snapshot()
        side_effects = (self._side_effects(final)
                        - self._side_effects(baseline))

        responses = [a.response for a in attempts if a.outcome == "ok"]
        distinct: List[Any] = []
        for r in responses:
            if r not in distinct:
                distinct.append(r)
        return_consistent = len(distinct) <= 1
        lost = any(a.outcome != "ok" for a in attempts)
        if return_consistent and len(responses) == 1 and lost:
            return_consistent = self._anchored_consistency(
                responses[0], attempts, baseline, final)

        verdict = judge(side_effects, return_consistent)
        return PatternResult(
            pattern=pattern,
            attempts=attempts,
            side_effect_count=side_effects,
            distinct_responses=distinct,
            return_consistent=return_consistent,
            state_trace=[a.snapshot_after for a in attempts],
            verdict=verdict,
            rationale=_rationale(verdict, side_effects, return_consistent),
        )

    # -- patterns -------------------------------------------------------------

    def run_sequential_replay(self) -> PatternResult:
        self.target.reset()
        baseline = self._snapshot()
        attempts = []
        for i in range(self.sequential_repeats):
            attempts.append(self._one_attempt(i))
        return self._analyze("sequential_replay", attempts, baseline)

    def run_concurrent_replay(self) -> PatternResult:
        self.target.reset()
        baseline = self._snapshot()
        barrier = threading.Barrier(self.concurrent_workers)
        results: List[Optional[Attempt]] = [None] * self.concurrent_workers

        def worker(idx: int) -> None:
            barrier.wait()  # maximize overlap
            results[idx] = self._one_attempt(idx)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(self.concurrent_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return self._analyze("concurrent_replay", results, baseline)

    def run_timeout_then_retry(self) -> PatternResult:
        self.target.reset()
        if hasattr(self.target, "arm_timeout"):
            self.target.arm_timeout(1)  # first attempt: applied, then "timeout"
        baseline = self._snapshot()
        attempts = [self._one_attempt(0)]
        if attempts[0].outcome == "timeout":
            time.sleep(self.retry_delay_seconds)
            attempts.append(self._one_attempt(1))
        else:
            # target did not simulate a timeout; still retry once so the
            # pattern remains meaningful
            attempts.append(self._one_attempt(1))
        return self._analyze("timeout_then_retry", attempts, baseline)

    def _one_attempt(self, index: int) -> Attempt:
        try:
            response = self.target.execute(copy.deepcopy(self.request))
            return Attempt(index=index, outcome="ok", response=response,
                           snapshot_after=self._snapshot())
        except SimulatedTimeout as exc:
            return Attempt(index=index, outcome="timeout",
                           error=str(exc),
                           snapshot_after=self._snapshot())
        except Exception as exc:  # noqa: BLE001 - evidence, not failure
            return Attempt(index=index, outcome="error",
                           error=f"{type(exc).__name__}: {exc}",
                           snapshot_after=self._snapshot())

    # -- entry point ----------------------------------------------------------

    def run_all(self) -> Report:
        report = Report(target_name=type(self.target).__name__,
                        request=self.request)
        report.patterns.append(self.run_sequential_replay())
        report.patterns.append(self.run_concurrent_replay())
        report.patterns.append(self.run_timeout_then_retry())
        report.finalize()
        return report
