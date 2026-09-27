"""experiments.py - run all Monte Carlo experiments and write results/ data.

Usage: python3 experiments.py
Outputs: results/convergence.csv, results/coverage.csv,
         results/variance_reduction.csv, results/edge_cases.csv
         (+ human-readable tables and an ASCII error curve on stdout)
"""

import csv
import math
import os
import random
import time

from mcint import crude_mc, stratified_mc, importance_sampling, control_variate

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
os.makedirs(RESULTS, exist_ok=True)

Z95 = 1.959964


def linregress_slope(xs, ys):
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den


def ascii_loglog(ns, errs, width=56, height=16):
    """Tiny log2-log2 ASCII plot of err vs n."""
    xs = [math.log2(n) for n in ns]
    ys = [math.log2(e) if e > 0 else min(math.log2(e2) for e2 in errs if e2 > 0) - 1
          for e in errs]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    grid = [[" "] * width for _ in range(height)]
    for x, y in zip(xs, ys):
        cx = round((x - x0) / (x1 - x0) * (width - 1)) if x1 > x0 else 0
        cy = round((y1 - y) / (y1 - y0) * (height - 1)) if y1 > y0 else 0
        grid[cy][cx] = "*"
    lines = ["  log2(err)"]
    for r, row in enumerate(grid):
        ylab = f"{y1 - r * (y1 - y0) / (height - 1):7.1f}" if r % 3 == 0 else " " * 7
        lines.append(f"{ylab} |" + "".join(row))
    lines.append(" " * 8 + "+" + "-" * width)
    lines.append(f"{'':8} {x0:<8.0f}{'log2(N)':^{width - 16}}{x1:>8.0f}")
    return "\n".join(lines)


# ---------------------------------------------------------------- problems
def make_exp_sum(dim):
    """f(x) = exp(sum x_i) on [0,1]^dim, integral = (e-1)^dim."""
    def f(x):
        return math.exp(sum(x))
    return f, (math.e - 1.0) ** dim


def make_box_indicator(dim, side):
    """Indicator of [0,side]^dim inside [0,1]^dim; integral = side**dim."""
    def f(x):
        return 1.0 if all(xi <= side for xi in x) else 0.0
    return f, side ** dim


def make_gaussian_spike(dim, center=0.5, sigma=0.01):
    """Gaussian bump exp(-|x-c|^2/(2 sigma^2)) on [0,1]^dim (analytic via erf)."""
    def f(x):
        return math.exp(-sum((xi - center) ** 2 for xi in x) / (2.0 * sigma * sigma))
    one_dim = sigma * math.sqrt(math.pi / 2.0) * (
        math.erf((1.0 - center) / (sigma * math.sqrt(2.0)))
        - math.erf((0.0 - center) / (sigma * math.sqrt(2.0))))
    return f, one_dim ** dim


def make_slab(dim, eps):
    """f=1 on the degenerate slab 0<=x0<=eps (volume eps), 0 elsewhere."""
    def f(x):
        return 1.0 if x[0] <= eps else 0.0
    return f, eps


# ---------------------------------------------------------------- 1. convergence
def experiment_convergence():
    print("=" * 72)
    print("1. CONVERGENCE: error vs N (theory: stderr ~ N^-1/2, slope -0.5)")
    print("=" * 72)
    dim = 4
    f, true_val = make_exp_sum(dim)
    rows = []
    for method, runner in (
        ("crude", lambda n, rng: crude_mc(f, dim, n, rng)),
        ("stratified", lambda n, rng: stratified_mc(f, dim, n, rng, strata=4)),
    ):
        ns, errs, ses = [], [], []
        print(f"\n[{method}]  true = {true_val:.8f}")
        print(f"{'N':>10} {'estimate':>12} {'abs_err':>11} {'stderr':>11} {'time(s)':>8}")
        for k in range(10, 21, 2):
            n = 2 ** k
            rng = random.Random(1234 + k)
            res = runner(n, rng)
            err = abs(res.estimate - true_val)
            ns.append(res.n)
            errs.append(max(err, 1e-16))
            ses.append(res.stderr)
            rows.append([method, res.n, f"{res.estimate:.8f}", f"{err:.6e}",
                         f"{res.stderr:.6e}", f"{res.elapsed:.4f}"])
            print(f"{res.n:>10} {res.estimate:>12.6f} {err:>11.3e} "
                  f"{res.stderr:>11.3e} {res.elapsed:>8.3f}")
        lns = [math.log2(n) for n in ns]
        slope = linregress_slope(lns, [math.log2(e) for e in errs])
        slope_se = linregress_slope(lns, [math.log2(s) for s in ses])
        print(f"empirical slopes (log2-log2): |error| {slope:+.3f}, "
              f"stderr {slope_se:+.3f}  (theory: -0.500)")
        print(ascii_loglog(ns, errs))
    with open(os.path.join(RESULTS, "convergence.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["method", "n", "estimate", "abs_error", "stderr", "elapsed_s"])
        w.writerows(rows)
    print(f"\nwrote {os.path.join(RESULTS, 'convergence.csv')}")


# ---------------------------------------------------------------- 2. coverage
def experiment_coverage():
    print("\n" + "=" * 72)
    print("2. VALIDATION vs ANALYTIC: 95% CI coverage over independent replications")
    print("=" * 72)
    dim, n, reps = 4, 4000, 400
    f, true_val = make_exp_sum(dim)
    rows = []
    for method, runner in (
        ("crude", lambda rng: crude_mc(f, dim, n, rng)),
        ("stratified", lambda rng: stratified_mc(f, dim, n, rng, strata=4)),
        ("control_variate", lambda rng: control_variate(
            f, lambda x: 1.0 + sum(x), 1.0 + dim * 0.5, dim, n, rng)),
    ):
        hits = 0
        se_sum = 0.0
        for r in range(reps):
            res = runner(random.Random(90000 + r))
            lo, hi = res.ci(Z95)
            hits += (lo <= true_val <= hi)
            se_sum += res.stderr
        cov = hits / reps
        rows.append([method, n, reps, f"{cov:.4f}", f"{se_sum / reps:.6e}"])
        print(f"{method:<16} N={n:<6} reps={reps}  "
              f"coverage={cov:.3f} (target ~0.95)  mean_stderr={se_sum / reps:.3e}")
    with open(os.path.join(RESULTS, "coverage.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["method", "n_per_rep", "reps", "coverage_95", "mean_stderr"])
        w.writerows(rows)
    print(f"wrote {os.path.join(RESULTS, 'coverage.csv')}")


# ---------------------------------------------------------------- 3. variance reduction
def experiment_variance_reduction():
    print("\n" + "=" * 72)
    print("3. VARIANCE REDUCTION on the same smooth integrand (same N, same seed)")
    print("=" * 72)
    dim, n = 4, 200_000
    f, true_val = make_exp_sum(dim)
    g = lambda x: 1.0 + sum(x)                    # Taylor proxy of exp(sum x)
    g_int = 1.0 + dim * 0.5                             # integral of g over [0,1]^dim
    base_seed = 777
    res = {}
    res["crude"] = crude_mc(f, dim, n, random.Random(base_seed))
    res["stratified(8/dim)"] = stratified_mc(f, dim, n, random.Random(base_seed), strata=8)
    res["control_variate"] = control_variate(f, g, g_int, dim, n, random.Random(base_seed))
    # importance sampling with proposal q(x) ∝ exp(sum x_i) truncated to [0,1]^4
    # (exactly matched here to demonstrate the ideal case; inverse-CDF sampler)
    lam = 1.0
    z1 = (math.exp(lam) - 1.0) / lam  # per-coordinate normaliser
    def sample_q(rng):
        return [math.log(1.0 + rng.random() * (math.exp(lam) - 1.0)) / lam
                for _ in range(dim)]
    def pdf_q(x):
        if any(xi < 0.0 or xi > 1.0 for xi in x):
            return 0.0
        return math.exp(lam * sum(x)) / (z1 ** dim)
    res["importance(matched)"] = importance_sampling(
        f, dim, n, random.Random(base_seed), sample_q, pdf_q)
    print(f"true value = {true_val:.8f},  N = {n}")
    print(f"{'method':<22} {'estimate':>12} {'stderr':>11} {'var reduction':>13} {'time(s)':>8}")
    base_var = res["crude"].stderr ** 2
    rows = []
    for name, r in res.items():
        vr = base_var / (r.stderr ** 2)
        rows.append([name, n, f"{r.estimate:.8f}", f"{r.stderr:.6e}",
                     f"{vr:.4g}", f"{r.elapsed:.4f}"])
        print(f"{name:<22} {r.estimate:>12.6f} {r.stderr:>11.3e} {vr:>13.3g}x {r.elapsed:>8.3f}")
    with open(os.path.join(RESULTS, "variance_reduction.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["method", "n", "estimate", "stderr", "variance_reduction", "elapsed_s"])
        w.writerows(rows)
    print(f"wrote {os.path.join(RESULTS, 'variance_reduction.csv')}")


# ---------------------------------------------------------------- 4. edge cases
def experiment_edge_cases():
    print("\n" + "=" * 72)
    print("4. PATHOLOGICAL CASES (sample size + wall-clock time)")
    print("=" * 72)
    rows = []

    # (a) function almost everywhere zero: indicator of [0,0.05]^4
    dim, side, n = 4, 0.05, 1_000_000
    f, true_val = make_box_indicator(dim, side)
    r1 = crude_mc(f, dim, n, random.Random(1))
    # importance sampling: proposal uniform on the small box itself
    lo, hi = [0.0] * dim, [side] * dim
    vol_small = side ** dim
    r2 = importance_sampling(
        f, dim, n, random.Random(1),
        lambda rng: [rng.uniform(0.0, side) for _ in range(dim)],
        lambda x: 1.0 / vol_small if all(0.0 <= xi <= side for xi in x) else 0.0)
    print(f"\n(a) almost-everywhere-zero indicator, [0,{side}]^{dim}, true={true_val:.3e}, N={n}")
    for name, r in (("crude", r1), ("importance", r2)):
        rel = abs(r.estimate - true_val) / true_val
        print(f"  {name:<11} est={r.estimate:.6e}  stderr={r.stderr:.3e}  "
              f"rel_err={rel:.3%}  n={r.n}  time={r.elapsed:.2f}s")
        rows.append(["ae_zero", name, r.n, f"{r.estimate:.8e}", f"{r.stderr:.4e}",
                     f"{true_val:.6e}", f"{r.elapsed:.4f}"])

    # (b) narrow Gaussian spike, sigma=0.01 in d=5
    dim, sigma, n = 5, 0.01, 1_000_000
    f, true_val = make_gaussian_spike(dim, 0.5, sigma)
    r1 = crude_mc(f, dim, n, random.Random(2))
    norm = 1.0 / (sigma * math.sqrt(2.0 * math.pi))
    r2 = importance_sampling(
        f, dim, n, random.Random(2),
        lambda rng: [rng.gauss(0.5, sigma) for _ in range(dim)],
        lambda x: math.prod(norm * math.exp(-((xi - 0.5) ** 2) / (2 * sigma * sigma))
                            for xi in x))
    print(f"\n(b) Gaussian spike sigma={sigma} in d={dim}, true={true_val:.6e}, N={n}")
    for name, r in (("crude", r1), ("importance", r2)):
        rel = abs(r.estimate - true_val) / true_val if true_val else float("nan")
        print(f"  {name:<11} est={r.estimate:.6e}  stderr={r.stderr:.3e}  "
              f"rel_err={rel:.3%}  n={r.n}  time={r.elapsed:.2f}s")
        rows.append(["spike", name, r.n, f"{r.estimate:.8e}", f"{r.stderr:.4e}",
                     f"{true_val:.6e}", f"{r.elapsed:.4f}"])

    # (c) degenerate region: thin slab 0<=x0<=1e-3 in d=6
    dim, eps, n = 6, 1e-3, 1_000_000
    f, true_val = make_slab(dim, eps)
    r1 = crude_mc(f, dim, n, random.Random(3))
    # stratify heavily along the degenerate axis: 2000 strata on x0, 1 elsewhere
    r2 = stratified_mc(f, dim, n, random.Random(3), strata=[2000] + [1] * (dim - 1))
    print(f"\n(c) degenerate slab 0<=x0<={eps}, d={dim}, true={true_val:.3e}, N={n}")
    for name, r in (("crude", r1), ("stratified", r2)):
        rel = abs(r.estimate - true_val) / true_val
        print(f"  {name:<11} est={r.estimate:.6e}  stderr={r.stderr:.3e}  "
              f"rel_err={rel:.3%}  n={r.n}  time={r.elapsed:.2f}s")
        rows.append(["degenerate_slab", name, r.n, f"{r.estimate:.8e}",
                     f"{r.stderr:.4e}", f"{true_val:.6e}", f"{r.elapsed:.4f}"])

    with open(os.path.join(RESULTS, "edge_cases.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["case", "method", "n", "estimate", "stderr", "true", "elapsed_s"])
        w.writerows(rows)
    print(f"\nwrote {os.path.join(RESULTS, 'edge_cases.csv')}")


if __name__ == "__main__":
    t0 = time.perf_counter()
    experiment_convergence()
    experiment_coverage()
    experiment_variance_reduction()
    experiment_edge_cases()
    print(f"\ntotal experiment time: {time.perf_counter() - t0:.1f}s")
