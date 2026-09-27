"""Priority-aware sampling, per-dimension rate limiting and duplicate
suppression for telemetry collectors. Standard library only.

Pipeline (Sampler.process), every rule is explainable via Sampler.explain()
and accounted for in Sampler.stats():

  R1  error-flagged event              -> ALWAYS kept
  R2  value >= spike_threshold         -> ALWAYS kept (spike protection)
  R3  first occurrence of a (dims, message) pattern in the current window
      (only for events with dedup=True) -> ALWAYS kept, never sampled
  R4  repeat of a known pattern within window_seconds
                                       -> suppressed and counted
  R5  normal event beyond max_per_combo_per_window for its dimension combo
                                       -> dropped (rate limit)
  R6  remaining normal event           -> kept with probability sample_rate
                                          (deterministic blake2b hashing)

Guarantees: rare-but-important signals (errors R1, spikes R2, brand-new log
patterns R3) survive any burst; only normal, already-seen or over-quota data
is ever dropped (R4/R5/R6), and every drop is counted per rule.

Cardinality protection: the pattern table is bounded by max_patterns; when
full, the oldest 10% of entries are evicted (counted as pattern_evictions),
so memory stays flat even under a cardinality explosion.
"""

import hashlib
import json
import time
from collections import defaultdict

_EVICT_FRACTION = 0.1


class Event:
    """A single telemetry event from the collection side."""

    __slots__ = ("dims", "message", "value", "is_error", "dedup", "ts")

    def __init__(self, dims, message, value=0.0, is_error=False,
                 dedup=False, ts=None):
        self.dims = dict(dims)          # e.g. {"service": "api", "level": "info"}
        self.message = message
        self.value = float(value)
        self.is_error = bool(is_error)
        self.dedup = bool(dedup)        # log-like events opt into dedup
        self.ts = time.time() if ts is None else float(ts)

    def dim_key(self):
        return tuple(sorted(self.dims.items()))

    def fingerprint(self):
        return (self.dim_key(), self.message)

    def to_json(self):
        return json.dumps({
            "dims": self.dims, "message": self.message,
            "value": self.value, "is_error": self.is_error, "ts": self.ts,
        }, sort_keys=True)


class _ComboState:
    __slots__ = ("accepted_in_window", "window_start")

    def __init__(self, now):
        self.accepted_in_window = 0
        self.window_start = now


class Sampler:
    def __init__(self, sample_rate=0.1, spike_threshold=1000.0,
                 max_per_combo_per_window=100, window_seconds=60.0,
                 max_patterns=100000, seed=b"gsb-sampler-v1"):
        if not 0.0 < sample_rate <= 1.0:
            raise ValueError("sample_rate must be in (0, 1]")
        self.sample_rate = float(sample_rate)
        self.spike_threshold = float(spike_threshold)
        self.max_per_combo = int(max_per_combo_per_window)
        self.window = float(window_seconds)
        self.max_patterns = int(max_patterns)
        self._seed = seed
        self._combos = {}                     # dim_key -> _ComboState
        self._patterns = {}                   # fingerprint -> first-seen ts
        self._suppressed = defaultdict(int)   # fingerprint -> count
        self._stats = defaultdict(lambda: defaultdict(int))

    # -- deterministic, explainable sampling --------------------------------
    def _sample_in(self, event):
        """Stable hash sampling: identical event content -> identical decision,
        so kept/dropped sets are reproducible and auditable."""
        h = hashlib.blake2b(self._seed, digest_size=8)
        h.update(event.to_json().encode("utf-8"))
        bucket = int.from_bytes(h.digest(), "big") / 2 ** 64
        return bucket < self.sample_rate

    def _evict_if_full(self):
        if len(self._patterns) < self.max_patterns:
            return
        n_evict = max(1, int(self.max_patterns * _EVICT_FRACTION))
        oldest = sorted(self._patterns, key=self._patterns.get)[:n_evict]
        for fp in oldest:
            del self._patterns[fp]
            self._suppressed.pop(fp, None)
        self._stats["pattern_evictions"]["dropped"] += n_evict

    # -- main entry ----------------------------------------------------------
    def process(self, event):
        """Return True if the event should be reported, False if dropped."""
        now = event.ts
        if event.is_error:                                  # R1
            self._stats["error"]["kept"] += 1
            return True
        if event.value >= self.spike_threshold:             # R2
            self._stats["spike"]["kept"] += 1
            return True

        if event.dedup:
            fp = event.fingerprint()
            first_seen = self._patterns.get(fp)
            if first_seen is not None and now - first_seen < self.window:
                # R4: known pattern inside its window -> suppress
                self._suppressed[fp] += 1
                self._stats["duplicate_suppressed"]["dropped"] += 1
                return False
            # R3: first occurrence (or first of a new window) -> always kept
            self._evict_if_full()
            self._patterns[fp] = now
            self._stats["new_pattern"]["kept"] += 1
            return True

        # Normal stream data: per-dimension-combo rate limit, then sampling.
        key = event.dim_key()
        state = self._combos.get(key)
        if state is None:
            state = self._combos[key] = _ComboState(now)
        elif now - state.window_start >= self.window:
            state.window_start = now
            state.accepted_in_window = 0
        if state.accepted_in_window >= self.max_per_combo:  # R5
            self._stats["normal_rate_limited"]["dropped"] += 1
            return False
        if not self._sample_in(event):                      # R6
            self._stats["normal_sampled"]["dropped"] += 1
            return False
        state.accepted_in_window += 1
        self._stats["normal_sampled"]["kept"] += 1
        return True

    # -- explainability ------------------------------------------------------
    def stats(self):
        out = {}
        total_kept = total_dropped = 0
        for cat in sorted(self._stats):
            kept = self._stats[cat].get("kept", 0)
            dropped = self._stats[cat].get("dropped", 0)
            total = kept + dropped
            out[cat] = {
                "kept": kept,
                "dropped": dropped,
                "effective_sample_rate": (kept / total) if total else None,
            }
            total_kept += kept
            total_dropped += dropped
        out["_total"] = {
            "kept": total_kept,
            "dropped": total_dropped,
            "effective_sample_rate": (
                total_kept / (total_kept + total_dropped)
                if (total_kept + total_dropped) else None
            ),
        }
        return out

    def suppression_report(self):
        """First-kept / later-suppressed evidence per pattern."""
        return [
            {
                "dims": dict(fp[0]),
                "message": fp[1],
                "first_seen_ts": self._patterns[fp],
                "suppressed_count": self._suppressed.get(fp, 0),
            }
            for fp in sorted(self._patterns, key=str)
        ]

    def explain(self):
        return [
            "R1 error-flagged events are always kept",
            "R2 events with value >= spike_threshold are always kept",
            "R3 first occurrence of a (dims, message) pattern per window "
            "is always kept (dedup=True events only)",
            "R4 repeats of a pattern within window_seconds are suppressed "
            "and counted (see suppression_report)",
            "R5 normal events beyond max_per_combo_per_window for their "
            "dimension combo are dropped",
            "R6 remaining normal events are kept with probability "
            "sample_rate via deterministic blake2b hash sampling",
            "R7 pattern table is capped at max_patterns; oldest 10% are "
            "evicted when full (counted as pattern_evictions)",
        ]
