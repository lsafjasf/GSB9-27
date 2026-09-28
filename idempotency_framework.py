"""Idempotency verification framework (Python 3, standard library only).

Verifies whether an interface that *claims* to be idempotent actually is,
by replaying the same logical request under multiple patterns and observing
three kinds of evidence:

  1. side-effect counts   (counters bumped, records written, notifications sent)
  2. return-value consistency across replays
  3. state-change trajectory (snapshot before/after every call)

Verdict categories:
  IDEMPOTENT                              - effects applied exactly once AND
                                            responses consistent AND state
                                            settled after the first call
  RETURN_CONSISTENT_BUT_EFFECTS_REPEATED  - responses look identical but the
                                            side effects ran more than once
                                            ("fake idempotent")
  NOT_IDEMPOTENT                          - everything else (responses diverge,
                                            or state keeps changing, etc.)
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


# --------------------------------------------------------------------------
# Effect recording
# --------------------------------------------------------------------------

class EffectKind(str, Enum):
    COUNTER = "counter"    # e.g. "charge" counter incremented
    WRITE = "write"        # e.g. record appended to a ledger / DB
    NOTIFY = "notify"      # e.g. email / webhook / message sent


class EffectRecorder:
    """Thread-safe log of every side effect performed by the SUT.

    The system under test receives this recorder and must route every
    observable side effect through it (that is the test harness contract).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self.events: List[Dict[str, Any]] = []

    def record(self, kind: EffectKind, detail: Any = None) -> None:
        with self._lock:
            self._seq += 1
            self.events.append(
                {
                    "seq": self._seq,
                    "ts": time.time(),
                    "kind": kind.value if isinstance(kind, EffectKind) else str(kind),
                    "detail": detail,
                }
            )

    def counts(self) -> Dict[str, int]:
        with self._lock:
            out: Dict[str, int] = {}
            for e in self.events:
                out[e["kind"]] = out.get(e["kind"], 0) + 1
            return out

    def total(self) -> int:
        with self._lock:
            return len(self.events)


# --------------------------------------------------------------------------
# System under test
# --------------------------------------------------------------------------

class SystemUnderTest:
    """Interface the implementation under test must satisfy."""

    name = "unnamed"

    def handle(self, request: Dict[str, Any], effects: EffectRecorder) -> Any:
        raise NotImplementedError

    def snapshot_state(self) -> Any:
        """Return a comparable snapshot of internal state."""
        raise NotImplementedError


# --------------------------------------------------------------------------
# Call traces (evidence)
# --------------------------------------------------------------------------

@dataclass
class CallTrace:
    pattern: str
    index: int
    response: Any = None
    delivered: bool = True          # False => simulated client-side timeout
    error: Optional[str] = None
    state_before: Any = None
    state_after: Any = None
    state_changed: bool = False
    effects_before: int = 0
    effects_after: int = 0

    @property
    def effects_delta(self) -> int:
        return self.effects_after - self.effects_before


@dataclass
class PatternResult:
    pattern: str
    traces: List[CallTrace] = field(default_factory=list)
    effects_before: Dict[str, int] = field(default_factory=dict)
    effects_after: Dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Replay patterns
# --------------------------------------------------------------------------

def _invoke(sut: SystemUnderTest, request, effects, pattern, index,
            delivered=True) -> CallTrace:
    trace = CallTrace(pattern=pattern, index=index, delivered=delivered)
    trace.state_before = sut.snapshot_state()
    trace.effects_before = effects.total()
    try:
        trace.response = sut.handle(request, effects)
    except Exception as exc:  # evidence, not failure
        trace.error = f"{type(exc).__name__}: {exc}"
    trace.effects_after = effects.total()
    trace.state_after = sut.snapshot_state()
    trace.state_changed = trace.state_after != trace.state_before
    return trace


def run_sequential_replay(sut, request, effects, repeats=3) -> PatternResult:
    """Same request replayed one after another (client retry loop)."""
    result = PatternResult(pattern="sequential_replay",
                           effects_before=effects.counts())
    for i in range(repeats):
        result.traces.append(_invoke(sut, request, effects,
                                     "sequential_replay", i))
    result.effects_after = effects.counts()
    return result


def run_concurrent_replay(sut, request, effects, workers=8) -> PatternResult:
    """Same request fired from many threads at once (retry storm)."""
    result = PatternResult(pattern="concurrent_replay",
                           effects_before=effects.counts())
    barrier = threading.Barrier(workers)
    traces: List[Optional[CallTrace]] = [None] * workers

    def worker(i: int) -> None:
        barrier.wait()  # maximise overlap
        traces[i] = _invoke(sut, request, effects, "concurrent_replay", i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    result.traces = [t for t in traces if t is not None]
    result.effects_after = effects.counts()
    return result


def run_timeout_retry(sut, request, effects, retries=2) -> PatternResult:
    """Simulate: server processes the request but the client times out
    (response lost), so the client retries with the same request."""
    result = PatternResult(pattern="timeout_retry",
                           effects_before=effects.counts())
    for i in range(retries + 1):
        delivered = i == retries  # all but the last attempt "time out"
        result.traces.append(_invoke(sut, request, effects,
                                     "timeout_retry", i,
                                     delivered=delivered))
    result.effects_after = effects.counts()
    return result


DEFAULT_PATTERNS: Dict[str, Callable[..., PatternResult]] = {
    "sequential_replay": run_sequential_replay,
    "concurrent_replay": run_concurrent_replay,
    "timeout_retry": run_timeout_retry,
}


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------

class Category(str, Enum):
    IDEMPOTENT = "IDEMPOTENT"
    RETURN_CONSISTENT_BUT_EFFECTS_REPEATED = (
        "RETURN_CONSISTENT_BUT_EFFECTS_REPEATED"
    )
    NOT_IDEMPOTENT = "NOT_IDEMPOTENT"


def _canon(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        return repr(value)


@dataclass
class Verdict:
    sut_name: str
    category: Category
    reasons: List[str]
    evidence: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sut": self.sut_name,
            "category": self.category.value,
            "reasons": self.reasons,
            "evidence": self.evidence,
        }


def evaluate(sut_name: str, patterns: List[PatternResult],
             total_effects: Dict[str, int]) -> Verdict:
    all_traces = [t for p in patterns for t in p.traces]
    errors = [t for t in all_traces if t.error]

    # --- evidence 1: side-effect counts ---------------------------------
    # One logical request => every effect kind must fire exactly once in
    # total across ALL replay patterns.
    repeated = {k: v for k, v in total_effects.items() if v != 1}
    effects_once = not repeated and bool(total_effects)

    # --- evidence 2: return-value consistency ----------------------------
    distinct = {_canon(t.response) for t in all_traces if not t.error}
    responses_consistent = len(distinct) <= 1 and not errors

    # --- evidence 3: state-change trajectory -----------------------------
    state_changes = sum(1 for t in all_traces if t.state_changed)
    state_settled = state_changes <= 1

    reasons: List[str] = []
    if effects_once:
        reasons.append(
            f"side effects applied exactly once across all replays: {total_effects}"
        )
    elif not total_effects:
        reasons.append("no side effects recorded at all (request had no effect?)")
    else:
        reasons.append(
            f"side effects repeated for one logical request: {total_effects}"
        )
    if responses_consistent:
        reasons.append(
            f"all {len(all_traces)} calls returned an identical response"
        )
    else:
        reasons.append(
            f"responses diverged ({len(distinct)} distinct values"
            + (f", {len(errors)} calls raised errors" if errors else "")
            + ")"
        )
    if state_settled:
        reasons.append(
            f"state changed {state_changes} time(s) across {len(all_traces)} calls"
        )
    else:
        reasons.append(
            f"state kept changing ({state_changes} changes across "
            f"{len(all_traces)} calls)"
        )

    if effects_once and responses_consistent and state_settled:
        category = Category.IDEMPOTENT
    elif responses_consistent and not effects_once:
        category = Category.RETURN_CONSISTENT_BUT_EFFECTS_REPEATED
    else:
        category = Category.NOT_IDEMPOTENT

    evidence = {
        "effect_counts_total": total_effects,
        "effect_counts_per_pattern": {
            p.pattern: {
                "before": p.effects_before,
                "after": p.effects_after,
            }
            for p in patterns
        },
        "calls_total": len(all_traces),
        "calls_with_errors": len(errors),
        "distinct_responses": len(distinct),
        "state_changes": state_changes,
        "state_trajectory": [
            {
                "pattern": t.pattern,
                "call": t.index,
                "delivered": t.delivered,
                "state_changed": t.state_changed,
                "effects_delta": t.effects_delta,
                "response": t.response,
                "error": t.error,
            }
            for t in all_traces
        ],
    }
    return Verdict(sut_name=sut_name, category=category,
                   reasons=reasons, evidence=evidence)


# --------------------------------------------------------------------------
# Driver + report
# --------------------------------------------------------------------------

def verify(sut: SystemUnderTest, request: Dict[str, Any],
           patterns: Optional[Dict[str, Callable]] = None) -> Verdict:
    """Run every replay pattern against a fresh SUT and produce a verdict."""
    effects = EffectRecorder()
    pattern_map = patterns or DEFAULT_PATTERNS
    results = [fn(sut, request, effects) for fn in pattern_map.values()]
    return evaluate(sut.name, results, effects.counts())


def format_text_report(verdict: Verdict) -> str:
    lines = [
        "=" * 72,
        f"Idempotency report for: {verdict.sut_name}",
        "=" * 72,
        f"VERDICT: {verdict.category.value}",
        "",
        "Reasons:",
    ]
    lines += [f"  - {r}" for r in verdict.reasons]
    ev = verdict.evidence
    lines += [
        "",
        "Evidence summary:",
        f"  total calls           : {ev['calls_total']}",
        f"  calls with errors     : {ev['calls_with_errors']}",
        f"  distinct responses    : {ev['distinct_responses']}",
        f"  state changes         : {ev['state_changes']}",
        f"  effect counts (total) : {ev['effect_counts_total']}",
        "",
        "State-change trajectory:",
        f"  {'pattern':<20}{'call':<6}{'delivered':<11}"
        f"{'state_changed':<15}{'effects_delta':<14}response/error",
    ]
    for t in ev["state_trajectory"]:
        tail = t["error"] if t["error"] else _canon(t["response"])
        if len(tail) > 60:
            tail = tail[:57] + "..."
        lines.append(
            f"  {t['pattern']:<20}{t['call']:<6}{str(t['delivered']):<11}"
            f"{str(t['state_changed']):<15}{t['effects_delta']:<14}{tail}"
        )
    lines.append("")
    return "\n".join(lines)


def format_json_report(verdict: Verdict) -> str:
    return json.dumps(verdict.to_dict(), indent=2, default=repr)
