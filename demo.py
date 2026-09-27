"""Demo / data generator for the adaptive RKF45 solver.

Runs the benchmark problems, compares against analytic solutions, prints
summary error data plus a sample of the step-size sequence, and writes
the full per-step logs to results/*.csv.

Run:  python3 demo.py
"""

import csv
import math
import os

from adaptive_rk import solve

RESULTS_DIR = "results"


def scenarios():
    return [
        ("decay", "y' = -y, y(0)=1, [0, 5]",
         lambda: solve(lambda t, y: -y, 0.0, 5.0, 1.0,
                       atol=1e-10, rtol=1e-9),
         lambda sol: abs(sol.y[-1] - math.exp(-5.0))),
        ("oscillator", "y1'=y2, y2'=-y1, y(0)=(1,0), [0, 2pi]",
         lambda: solve(lambda t, y: [y[1], -y[0]], 0.0, 2.0 * math.pi,
                       [1.0, 0.0], atol=1e-11, rtol=1e-9),
         lambda sol: math.hypot(sol.y[-1][0] - 1.0, sol.y[-1][1])),
        ("stiff", "eig(A) = {-1, -100}, y(0)=(2,0), [0, 1]",
         lambda: solve(lambda t, y: [-50.5 * y[0] + 49.5 * y[1],
                                     49.5 * y[0] - 50.5 * y[1]],
                       0.0, 1.0, [2.0, 0.0], atol=1e-10, rtol=1e-8),
         lambda sol: max(abs(sol.y[-1][0] - math.exp(-1.0) - math.exp(-100.0)),
                         abs(sol.y[-1][1] - math.exp(-1.0) + math.exp(-100.0)))),
        ("zero_ic", "y' = cos(t), y(0)=0, [0, pi]",
         lambda: solve(lambda t, y: math.cos(t), 0.0, math.pi, 0.0,
                       atol=1e-10, rtol=1e-9),
         lambda sol: abs(sol.y[-1] - math.sin(math.pi))),
        ("backward", "y' = -y, y(0)=1, [0, -3] (backwards)",
         lambda: solve(lambda t, y: -y, 0.0, -3.0, 1.0,
                       atol=1e-10, rtol=1e-9),
         lambda sol: abs(sol.y[-1] - math.exp(3.0))),
        ("discontinuous", "y' = 1 (t<1) / 0 (t>=1), y(0)=0, [0, 1]",
         lambda: solve(lambda t, y: 1.0 if t < 1.0 else 0.0,
                       0.0, 1.0, 0.0, atol=1e-10, rtol=1e-9),
         lambda sol: abs(sol.y[-1] - 1.0)),
    ]


def write_step_csv(name, sol):
    path = os.path.join(RESULTS_DIR, "steps_%s.csv" % name)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["step", "t", "h", "err_est", "accepted"])
        for s in sol.steps:
            w.writerow([s.index, "%.12e" % s.t, "%.6e" % s.h,
                        "%.6e" % s.err, int(s.accepted)])
    return path


def print_step_sample(name, sol, head=10, tail=5):
    steps = sol.steps
    shown = steps if len(steps) <= head + tail else steps[:head] + steps[-tail:]
    print("\n  step sequence sample [%s] (%d attempted steps):"
          % (name, len(steps)))
    print("  %6s %14s %12s %12s %s" % ("step", "t", "h", "err_est", "ok"))
    for i, s in enumerate(shown):
        if len(steps) > head + tail and i == head:
            print("  %6s" % "...")
        print("  %6d %14.8e %12.4e %12.4e %s"
              % (s.index, s.t, s.h, s.err, "yes" if s.accepted else "NO"))


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    summary_rows = []
    print("%-14s %-44s %9s %8s %8s %10s %10s"
          % ("problem", "description", "err", "accept", "reject",
             "h_min", "h_max"))
    print("-" * 104)
    for name, desc, run, err_fn in scenarios():
        sol = run()
        if not sol.ok:
            print("%-14s FAILED: %s" % (name, sol.message))
            continue
        err = err_fn(sol)
        acc = sol.accepted_steps
        h_lo = min(abs(s.h) for s in acc)
        h_hi = max(abs(s.h) for s in acc)
        print("%-14s %-44s %9.2e %8d %8d %10.2e %10.2e"
              % (name, desc, err, len(acc), len(sol.rejected_steps),
                 h_lo, h_hi))
        summary_rows.append([name, desc, "%.6e" % err, len(acc),
                             len(sol.rejected_steps), sol.nfev,
                             "%.3e" % h_lo, "%.3e" % h_hi])
        write_step_csv(name, sol)
        if name in ("decay", "stiff", "discontinuous"):
            print_step_sample(name, sol)

    # Failure-reporting demo: y' = y^2 blows up at t = 1.
    sol = solve(lambda t, y: y * y, 0.0, 2.0, 1.0,
                atol=1e-10, rtol=1e-8, h_min=1e-10)
    print("\nfailure demo: y' = y^2, y(0)=1, [0, 2] (blows up at t=1)")
    print("  status=%s, stopped at t=%.10f" % (sol.status, sol.t[-1]))
    print("  message: %s" % sol.message)
    summary_rows.append(["blowup", "y'=y^2, y(0)=1, [0,2]", "FAILED",
                         len(sol.accepted_steps), len(sol.rejected_steps),
                         sol.nfev, "", ""])
    write_step_csv("blowup", sol)

    path = os.path.join(RESULTS_DIR, "summary.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["problem", "description", "global_error", "accepted",
                    "rejected", "nfev", "h_min_used", "h_max_used"])
        w.writerows(summary_rows)
    print("\nfull step logs and summary written to %s/" % RESULTS_DIR)


if __name__ == "__main__":
    main()
