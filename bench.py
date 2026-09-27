"""Duration-distribution benchmark: legacy (buggy) vs fixed engine.

Runs deterministic per-structure scenarios plus 2000 randomized pipelines
through both engines on an injected FakeClock, and reports the elapsed-time
distribution against the agreed budget. All numbers are exact (virtual time).
"""
import random
import statistics
from itertools import count

from timeoutctl import Par, Retry, Seq, Step, run_legacy, run_pipeline

BUDGET = 100  # ms


# ------------------------------------------------------------ random pipelines

def random_spec(rng, ids, depth=0):
    if depth >= 3 or rng.random() < 0.45:
        return ("step", "s%d" % next(ids), rng.randint(10, 120),
                rng.choice([0, 0, 0, 1, 2, 3]))
    kind = rng.choice(("seq", "retry", "par"))
    name = "%s%d" % (kind, next(ids))
    if kind == "seq":
        return ("seq", name, [random_spec(rng, ids, depth + 1)
                              for _ in range(rng.randint(2, 4))])
    if kind == "retry":
        return ("retry", name, rng.randint(2, 3), random_spec(rng, ids, depth + 1))
    return ("par", name, [random_spec(rng, ids, depth + 1)
                          for _ in range(rng.randint(2, 3))])


def build(spec):
    kind = spec[0]
    if kind == "step":
        return Step(spec[1], spec[2], fail_times=spec[3])
    if kind == "seq":
        return Seq(spec[1], *[build(s) for s in spec[2]])
    if kind == "retry":
        return Retry(spec[1], build(spec[3]), attempts=spec[2])
    return Par(spec[1], *[build(s) for s in spec[2]])


def pct(sorted_vals, q):
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def stats_row(label, times, budget):
    t = sorted(times)
    over = sum(1 for x in t if x > budget)
    return ("| %s | %d | %d | %.0f | %.0f | %.0f | %.0f | %.1f | %d (%.1f%%) |"
            % (label, len(t), budget, t[0], pct(t, 0.50), pct(t, 0.95), t[-1],
               statistics.fmean(t), over, 100.0 * over / len(t)))


def main():
    print("# Timeout budget: duration distribution before/after the fix")
    print()
    print("Budget = %dms per pipeline. All times in ms, measured on an injected"
          " deterministic clock." % BUDGET)
    print()

    # -- deterministic per-structure scenarios -------------------------------
    scenarios = [
        ("single step 40ms", Step("a", 40)),
        ("serial 3 x 50ms", Seq("p", Step("a", 50), Step("b", 50), Step("c", 50))),
        ("retry 3 x 60ms (flaky)",
         Retry("r", Step("a", 60, fail_times=99), attempts=3)),
        ("nested retry 3x3 x 40ms",
         Retry("o", Retry("i", Step("a", 40, fail_times=99), 3), 3)),
        ("parallel 90ms + 70ms", Par("p", Step("a", 90), Step("b", 70))),
        ("serial 40ms + parallel 2 x 90ms",
         Seq("p", Step("a", 40), Par("f", Step("b", 90), Step("c", 90)))),
        ("repro pipeline (fetch+retry+fanout)",
         Seq("p", Step("a", 80),
             Retry("r", Step("b", 60, fail_times=2), 3),
             Par("f", Step("c", 90), Step("d", 70)))),
    ]
    print("## Per-structure elapsed time (deterministic scenarios)")
    print()
    print("| structure | legacy (buggy) | fixed | budget |")
    print("|---|---|---|---|")
    for label, pipe_factory in scenarios:
        _, legacy = run_legacy(pipe_factory, BUDGET)
        _, fixed = run_pipeline(pipe_factory, BUDGET)
        print("| %s | %dms%s | %dms | %dms |"
              % (label, legacy.elapsed,
                 " **OVER**" if legacy.elapsed > BUDGET else "",
                 fixed.elapsed, BUDGET))
    print()

    # -- randomized distribution ---------------------------------------------
    n = 2000
    rng = random.Random(20260928)
    legacy_times, fixed_times = [], []
    for _ in range(n):
        spec = random_spec(rng, count(1))
        legacy_times.append(run_legacy(build(spec), BUDGET)[1].elapsed)
        fixed_times.append(run_pipeline(build(spec), BUDGET)[1].elapsed)

    print("## Randomized pipelines (%d runs, seed=20260928)" % n)
    print()
    print("| engine | runs | budget | min | p50 | p95 | max | mean | over budget |")
    print("|---|---|---|---|---|---|---|---|---|")
    print(stats_row("legacy (buggy)", legacy_times, BUDGET))
    print(stats_row("fixed", fixed_times, BUDGET))
    print()

    assert all(t <= BUDGET for t in fixed_times), "fixed engine exceeded budget!"
    worst = max(fixed_times)
    print("Fixed engine: worst-case elapsed = %dms <= %dms budget "
          "(upper bound holds for all %d runs)." % (worst, BUDGET, n))
    print("Legacy engine: worst-case elapsed = %dms (%.1fx the budget)."
          % (max(legacy_times), max(legacy_times) / BUDGET))


if __name__ == "__main__":
    main()
