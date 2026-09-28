"""Synthetic benchmark: detection rate, false-positive rate, timing.

Scenarios (baseline always N(0,1), n_baseline = 1000):
  null       : N(0,1) vs N(0,1)            -> measures false positive rate
  variance   : N(0,1) vs N(0, sigma)       -> same mean, wider shape
  shift      : N(0,1) vs N(mu, 1)          -> location shift
  tail       : body identical, tails beyond |x|>2 stretched by 1.6x
  categorical: {a,b,c} uniform vs skewed frequencies

Each scenario runs TRIALS independent trials; detection rate = fraction of
trials where report["alert"] is True. Timing is per-score wall time.

Run: python3 benchmark.py  (writes RESULTS.md)
"""

import random
import statistics
import time

from drift_detector import CategoricalDriftDetector, NumericDriftDetector

TRIALS = 300
N_BASE = 1000


def gen_null(rng, n):
    return [rng.gauss(0, 1) for _ in range(n)]


def gen_variance(sigma):
    def g(rng, n):
        return [rng.gauss(0, sigma) for _ in range(n)]
    return g


def gen_shift(mu):
    def g(rng, n):
        return [rng.gauss(mu, 1) for _ in range(n)]
    return g


def gen_tail(threshold, factor):
    """Same body as N(0,1); samples beyond |x|>threshold are stretched, so
    the mean stays 0 and the variance is nearly unchanged while the tails
    fatten."""
    def g(rng, n):
        out = []
        while len(out) < n:
            x = rng.gauss(0, 1)
            if abs(x) > threshold:
                x *= factor
            out.append(x)
        return out
    return g


def gen_categorical_skewed(rng, n):
    return [rng.choice(("a", "a", "a", "b", "c")) for _ in range(n)]


def gen_categorical_null(rng, n):
    return [rng.choice(("a", "b", "c")) for _ in range(n)]


def run_numeric(name, gen_current, n_current=500, trials=TRIALS, seed=1000):
    alerts = 0
    psis, ks_ps, tail_ps, times = [], [], [], []
    for t in range(trials):
        rng = random.Random(seed + t)
        det = NumericDriftDetector().fit(gen_null(rng, N_BASE))
        cur = gen_current(rng, n_current)
        t0 = time.perf_counter()
        rep = det.score(cur)
        times.append((time.perf_counter() - t0) * 1e3)
        alerts += rep.alert
        psis.append(rep.psi)
        ks_ps.append(rep.ks_pvalue)
        tail_ps.append(rep.tail_pvalue)
    return {
        "name": name,
        "n": n_current,
        "rate": alerts / trials,
        "psi_med": statistics.median(psis),
        "ks_p_med": statistics.median(ks_ps),
        "tail_p_med": statistics.median(tail_ps),
        "ms_avg": statistics.mean(times),
        "ms_p95": sorted(times)[int(0.95 * trials)],
    }


def run_categorical(trials=TRIALS, seed=5000):
    rows = []
    for name, gen in (("cat_null", gen_categorical_null),
                      ("cat_skew", gen_categorical_skewed)):
        alerts, times = 0, []
        for t in range(trials):
            rng = random.Random(seed + t)
            det = CategoricalDriftDetector().fit(gen_categorical_null(rng, N_BASE))
            t0 = time.perf_counter()
            rep = det.score(gen(rng, 500))
            times.append((time.perf_counter() - t0) * 1e3)
            alerts += rep.alert
        rows.append({"name": name, "n": 500, "rate": alerts / trials,
                     "psi_med": float("nan"), "ks_p_med": float("nan"),
                     "tail_p_med": float("nan"),
                     "ms_avg": statistics.mean(times),
                     "ms_p95": sorted(times)[int(0.95 * trials)]})
    return rows


def fmt_row(r):
    return ("| {name} | {n} | {rate:.3f} | {psi:.4f} | {ksp:.3g} | {tp:.3g} | {ms:.3f} | {p95:.3f} |"
            .format(name=r["name"], n=r["n"], rate=r["rate"], psi=r["psi_med"],
                    ksp=r["ks_p_med"], tp=r["tail_p_med"], ms=r["ms_avg"], p95=r["ms_p95"]))


def main():
    rows = []
    # False positive rate at several batch sizes
    for n in (50, 200, 500):
        rows.append(run_numeric("null (FPR)", gen_null, n_current=n))
    # Variance change, same mean
    for sigma in (1.2, 1.5, 2.0):
        rows.append(run_numeric("variance sigma=%.1f" % sigma, gen_variance(sigma)))
    # Mean shift
    for mu in (0.1, 0.2, 0.5):
        rows.append(run_numeric("shift mu=%.1f" % mu, gen_shift(mu)))
    # Tail-only change
    rows.append(run_numeric("tail |x|>2.0 x1.6", gen_tail(2.0, 1.6)))
    rows.append(run_numeric("tail |x|>1.5 x1.6", gen_tail(1.5, 1.6)))
    # Small-sample behaviour
    rows.append(run_numeric("shift mu=0.5, n=30", gen_shift(0.5), n_current=30))
    rows.append(run_numeric("null, n=30 (FPR)", gen_null, n_current=30))
    rows.extend(run_categorical())

    header = ("| scenario | n_current | detect rate | median PSI | median KS p "
              "| median tail p | avg ms | p95 ms |\n|---|---|---|---|---|---|---|---|")
    body = "\n".join(fmt_row(r) for r in rows)
    table = header + "\n" + body
    print(table)
    with open("RESULTS.md", "w") as f:
        f.write("# Benchmark results\n\n")
        f.write("Trials per scenario: %d, baseline n=%d; alert = any calibrated "
                "p-value (binned G-test / KS / tail) < 0.01.\n\n"
                % (TRIALS, N_BASE))
        f.write(table + "\n")


if __name__ == "__main__":
    main()
