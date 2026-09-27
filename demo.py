"""Generates synthetic traffic for each required scenario and prints
sampling/drop statistics plus a pre-sampling vs post-sampling comparison
of in-memory footprint and reported volume. Run: python3 demo.py"""

import sys
import tracemalloc

from sampler import Event, Sampler

API = {"service": "api", "level": "info"}


def measure(events, sampler):
    """Feed events through the sampler; buffer what a reporter would hold."""
    tracemalloc.start()
    kept = [e for e in events if sampler.process(e)]
    buffer = [e.to_json() for e in kept]   # serialized report buffer
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del buffer
    return kept, peak


def baseline_memory(events):
    """Reporter buffer if we naively kept every event (old behaviour)."""
    tracemalloc.start()
    buffer = [e.to_json() for e in events]
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del buffer
    return peak


def report_bytes(events):
    return sum(len(e.to_json().encode("utf-8")) for e in events)


def fmt_bytes(n):
    for unit in ("B", "KiB", "MiB"):
        if n < 1024 or unit == "MiB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def run_scenario(name, events, sampler):
    kept, sampled_mem = measure(events, sampler)
    base_mem = baseline_memory(events)
    in_bytes = report_bytes(events)
    out_bytes = report_bytes(kept)

    print("=" * 74)
    print("SCENARIO: %s" % name)
    print("-" * 74)
    print("events in: %d   kept: %d   dropped: %d" %
          (len(events), len(kept), len(events) - len(kept)))
    print("memory (retained events, tracemalloc peak):")
    print("  before sampling: %-12s after sampling: %-12s reduction: %.1fx"
          % (fmt_bytes(base_mem), fmt_bytes(sampled_mem),
             base_mem / max(sampled_mem, 1)))
    print("report volume (JSON bytes):")
    print("  before sampling: %-12s after sampling: %-12s reduction: %.1fx"
          % (fmt_bytes(in_bytes), fmt_bytes(out_bytes),
             in_bytes / max(out_bytes, 1)))
    print("per-rule statistics (kept / dropped / effective sample rate):")
    for cat, c in sampler.stats().items():
        rate = c["effective_sample_rate"]
        print("  %-22s kept=%-7d dropped=%-7d rate=%s"
              % (cat, c["kept"], c["dropped"],
                 "n/a" if rate is None else "%.4f" % rate))
    return sampler


def main():
    # 1. Burst traffic: 100k normal metrics + a few errors and spikes mixed in.
    burst = ([Event(API, "metric-%d" % i, value=1.0, ts=0.0)
              for i in range(100_000)]
             + [Event(API, "db error", is_error=True, ts=0.0)
                for _ in range(20)]
             + [Event(API, "latency", value=99_999.0, ts=0.0)
                for _ in range(5)])
    s1 = run_scenario(
        "burst traffic (100k normal + 20 errors + 5 spikes)",
        burst, Sampler(sample_rate=0.05, spike_threshold=1000.0,
                       max_per_combo_per_window=10_000))
    st = s1.stats()
    assert st["error"]["kept"] == 20 and st["spike"]["kept"] == 5
    print("  -> all 20 errors and 5 spikes survived the burst")

    # 2. Everything over threshold: nothing may be dropped.
    all_spike = [Event(API, "hot-%d" % i, value=1e6, ts=0.0)
                 for i in range(20_000)]
    s2 = run_scenario("all events over spike threshold",
                      all_spike, Sampler(sample_rate=0.01,
                                         spike_threshold=1000.0))
    assert s2.stats()["_total"]["dropped"] == 0

    # 3. Cardinality explosion: 200k unique log patterns, table capped.
    explosion = [Event(API, "unique-pattern-%d" % i, dedup=True, ts=0.0)
                 for i in range(200_000)]
    s3 = run_scenario("cardinality explosion (200k unique patterns, "
                      "max_patterns=10k)",
                      explosion, Sampler(max_patterns=10_000,
                                         window_seconds=10 ** 9))
    assert len(s3._patterns) <= 10_000
    print("  -> pattern table capped at %d entries (evictions: %d)"
          % (len(s3._patterns),
             s3.stats()["pattern_evictions"]["dropped"]))

    # 4. Duplicate log suppression: first kept, rest suppressed.
    dupes = [Event(API, "connection reset by peer", dedup=True, ts=i * 0.001)
             for i in range(50_000)]
    s4 = run_scenario("duplicate log storm (50k identical lines)",
                      dupes, Sampler(window_seconds=3600.0))
    rep = s4.suppression_report()
    assert len(rep) == 1 and rep[0]["suppressed_count"] == 50_000 - 1
    print("  -> suppression report: %r kept once, %d repeats suppressed"
          % (rep[0]["message"], rep[0]["suppressed_count"]))

    # 5. No data.
    s5 = run_scenario("no data", [], Sampler())
    assert s5.stats()["_total"]["kept"] == 0

    print("=" * 74)
    print("DROP RULES (explainability):")
    for line in s1.explain():
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
